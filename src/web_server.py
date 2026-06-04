# -*- coding: utf-8 -*-
"""
web_server.py
================================================================================
FPS 多模态态势感知系统 —— 轻量 Web 雷达看板 (FastAPI + WebSocket)

定位
----
无侵入、极低延迟的本地副屏看板：订阅 EventBus 的 CV_UPDATE / FUSION_RESULT，
将本机坐标与敌人融合定位实时 JSON 广播给前端 Canvas 雷达。

线程桥接（关键）
----------------
EventBus 回调运行在**同步工作线程**，而 WebSocket 发送是 **asyncio 协程**。
二者用一个线程安全的有界队列 `_outbox` 解耦：
- 回调侧（任意线程）：仅 `json.dumps` 后入队；队满则丢弃最旧消息（背压，雷达只关心最新态势）。
- 异步侧（事件循环）：后台 `_broadcaster` 任务持续出队并扇出给所有连接，顺手剔除断开的连接。

这样回调永不阻塞、无锁竞争，且天然限制内存。
================================================================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from event_bus import EventBus, Topic

try:
    from config_manager import ConfigManager
except ImportError:  # pragma: no cover
    ConfigManager = None  # type: ignore

_LOGGER: logging.Logger = logging.getLogger("web_server")


# ===========================================================================
#  前端单文件页面（HTML + CSS + Vanilla JS Canvas 雷达）
# ===========================================================================
_RADAR_HTML: str = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<title>FPS 态势雷达</title>
<style>
  html,body{margin:0;height:100%;background:#05080a;color:#7fffd4;
            font-family:Consolas,Menlo,monospace;overflow:hidden}
  #hud{position:fixed;top:10px;left:12px;font-size:13px;line-height:1.5;z-index:10;
       text-shadow:0 0 6px #0f0a}
  #hud b{color:#9effa1}
  #status{color:#ff6b6b}
  canvas{display:block;margin:0 auto}
</style>
</head>
<body>
<div id="hud">
  <div><b>FPS 态势雷达</b> · Top-Down</div>
  <div>self: <span id="pos">--</span></div>
  <div>heading: <span id="hdg">--</span>°</div>
  <div>contacts: <span id="cnt">0</span></div>
  <div id="status">连接中…</div>
</div>
<canvas id="radar"></canvas>
<script>
const SCALE = 4;            // 像素/米
const ENEMY_LIFE = 2000;    // 红点余辉生命周期 (ms)
const RING_STEP_M = 10;     // 距离环间隔 (米)

const canvas = document.getElementById('radar');
const ctx = canvas.getContext('2d');
let W=0,H=0,CX=0,CY=0;
function resize(){
  W = canvas.width = Math.min(window.innerWidth, window.innerHeight) - 20;
  H = canvas.height = W; CX = W/2; CY = H/2;
}
window.addEventListener('resize', resize); resize();

// 状态
let player = {x:0, y:0, z:0, heading:0, conf:1};
let enemies = [];   // {x,y,z,conf,born}

// WebSocket
function connect(){
  const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/ws');
  ws.onopen   = ()=>{ document.getElementById('status').textContent='● 已连接';
                      document.getElementById('status').style.color='#9effa1'; };
  ws.onclose  = ()=>{ document.getElementById('status').textContent='○ 断线，重连中…';
                      document.getElementById('status').style.color='#ff6b6b';
                      setTimeout(connect, 1000); };
  ws.onerror  = ()=>ws.close();
  ws.onmessage= (ev)=>{
    let m; try{ m = JSON.parse(ev.data); }catch(e){ return; }
    if(m.type==='player'){
      player.x=m.x; player.y=m.y; player.z=m.z; player.heading=m.heading; player.conf=m.confidence;
    } else if(m.type==='enemy'){
      enemies.push({x:m.x, y:m.y, z:m.z, conf:m.confidence, born:performance.now()});
    }
  };
}
connect();

// 世界坐标 -> 屏幕坐标（玩家恒在中心；+Y 向上，故屏幕 y 取反）
function toScreen(wx, wy){
  return [CX + (wx - player.x)*SCALE, CY - (wy - player.y)*SCALE];
}

function drawGrid(){
  ctx.strokeStyle='rgba(40,90,70,0.6)'; ctx.lineWidth=1;
  // 距离环
  for(let r=RING_STEP_M; r*SCALE < Math.max(W,H); r+=RING_STEP_M){
    ctx.beginPath(); ctx.arc(CX,CY, r*SCALE, 0, Math.PI*2); ctx.stroke();
  }
  // 十字
  ctx.beginPath(); ctx.moveTo(CX,0); ctx.lineTo(CX,H);
  ctx.moveTo(0,CY); ctx.lineTo(W,CY); ctx.stroke();
}

function drawPlayer(){
  // heading: 0°指向+X，逆时针为正；屏幕 y 取反 -> 方向 (cos, -sin)
  const a = player.heading * Math.PI/180;
  const dx = Math.cos(a), dy = -Math.sin(a);
  const L = 16, Wd = 9;
  const tipx = CX + dx*L, tipy = CY + dy*L;          // 箭尖
  const bx = CX - dx*L*0.6, by = CY - dy*L*0.6;       // 箭尾中点
  const px = -dy, py = dx;                            // 垂直方向
  ctx.fillStyle='#39ff14'; ctx.shadowColor='#39ff14'; ctx.shadowBlur=12;
  ctx.beginPath();
  ctx.moveTo(tipx, tipy);
  ctx.lineTo(bx + px*Wd, by + py*Wd);
  ctx.lineTo(bx - px*Wd, by - py*Wd);
  ctx.closePath(); ctx.fill();
  ctx.shadowBlur=0;
}

function drawEnemies(now){
  let alive=[];
  for(const e of enemies){
    const age = now - e.born;
    if(age > ENEMY_LIFE) continue;
    alive.push(e);
    const k = 1 - age/ENEMY_LIFE;                 // 余辉衰减系数
    const [sx, sy] = toScreen(e.x, e.y);
    // 波纹
    ctx.strokeStyle = 'rgba(255,60,60,'+(k*0.6)+')'; ctx.lineWidth=2;
    ctx.beginPath(); ctx.arc(sx, sy, 6 + (1-k)*18, 0, Math.PI*2); ctx.stroke();
    // 实心红点
    ctx.fillStyle = 'rgba(255,40,40,'+k+')';
    ctx.shadowColor='#ff2828'; ctx.shadowBlur=10*k;
    ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI*2); ctx.fill();
    ctx.shadowBlur=0;
    // 置信度文本
    ctx.fillStyle='rgba(255,180,180,'+k+')'; ctx.font='11px monospace';
    ctx.fillText(e.conf.toFixed(2), sx+8, sy-8);
  }
  enemies = alive;
  document.getElementById('cnt').textContent = alive.length;
}

function loop(){
  const now = performance.now();
  ctx.clearRect(0,0,W,H);
  drawGrid(); drawEnemies(now); drawPlayer();
  document.getElementById('pos').textContent =
    player.x.toFixed(1)+', '+player.y.toFixed(1)+', '+player.z.toFixed(1);
  document.getElementById('hdg').textContent = player.heading.toFixed(0);
  requestAnimationFrame(loop);
}
loop();
</script>
</body>
</html>
"""


# ===========================================================================
#  Web 服务器
# ===========================================================================
class WebServer:
    """FastAPI 雷达看板：订阅总线 -> JSON -> WebSocket 广播。"""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        config_manager: Optional["ConfigManager"] = None,
        host: str = "127.0.0.1",
        port: int = 8000,
        outbox_maxsize: int = 256,
    ) -> None:
        self._bus: EventBus = event_bus or EventBus()
        self._cfg_mgr = config_manager or (ConfigManager() if ConfigManager else None)
        self.host = host
        self.port = port

        # 线程安全有界出队缓冲：回调侧入队，异步侧出队广播。
        self._outbox: "queue.Queue[str]" = queue.Queue(maxsize=max(1, outbox_maxsize))
        self._clients: Set[WebSocket] = set()
        self._broadcaster_task: Optional[asyncio.Task] = None
        self._running: bool = False

        self.app: FastAPI = self._build_app()

        # 订阅总线（回调在工作线程触发，仅做序列化 + 入队，绝不阻塞）。
        self._bus.subscribe(Topic.CV_UPDATE, self._on_cv_update)
        self._bus.subscribe(Topic.FUSION_RESULT, self._on_fusion_result)
        _LOGGER.info("WebServer 已订阅 CV_UPDATE / FUSION_RESULT。")

    # -----------------------------------------------------------------
    # 路由与生命周期
    # -----------------------------------------------------------------
    def _build_app(self) -> FastAPI:

        @asynccontextmanager
        async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
            # 启动：拉起广播任务
            self._running = True
            self._broadcaster_task = asyncio.create_task(self._broadcaster())
            _LOGGER.info("WebServer 广播任务已启动。")
            try:
                yield
            finally:
                # 关闭：停广播、断开所有连接
                self._running = False
                if self._broadcaster_task is not None:
                    self._broadcaster_task.cancel()
                for ws in list(self._clients):
                    try:
                        await ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                self._clients.clear()
                _LOGGER.info("WebServer 已关闭。")

        app = FastAPI(title="FPS Situational Radar", lifespan=_lifespan)

        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:  # noqa: D401
            """返回单文件雷达页面。"""
            return _RADAR_HTML

        @app.get("/health")
        async def health() -> Dict[str, Any]:
            return {"status": "ok", "clients": len(self._clients)}

        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket) -> None:
            await websocket.accept()
            self._clients.add(websocket)
            _LOGGER.info("WebSocket 客户端接入，当前在线 %d。", len(self._clients))
            try:
                # 服务端单向推送即可；这里持续读取以感知客户端断开。
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._clients.discard(websocket)
                _LOGGER.info("WebSocket 客户端断开，当前在线 %d。", len(self._clients))

        return app

    # -----------------------------------------------------------------
    # 总线回调（工作线程）—— 仅序列化 + 入队
    # -----------------------------------------------------------------
    def _on_cv_update(self, cv: Any) -> None:
        self._enqueue({
            "type": "player",
            "x": float(cv.x), "y": float(cv.y), "z": float(cv.z),
            "heading": float(cv.heading),
            "confidence": float(getattr(cv, "confidence", 1.0)),
            "frame_id": int(getattr(cv, "frame_id", -1)),
        })

    def _on_fusion_result(self, r: Any) -> None:
        self._enqueue({
            "type": "enemy",
            "x": float(r.enemy_x), "y": float(r.enemy_y), "z": float(r.enemy_z),
            "confidence": float(r.confidence),
            "material": str(getattr(r, "material_type", "mock")),
            "theta_world_deg": float(getattr(r, "theta_world_deg", 0.0)),
            "range_m": float(getattr(r, "range_m", 0.0)),
        })

    def _enqueue(self, payload: Dict[str, Any]) -> None:
        """序列化并入队；队满则丢弃最旧消息（雷达只关心最新态势）。"""
        try:
            msg = json.dumps(payload, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("广播消息序列化失败: %s", exc)
            return
        try:
            self._outbox.put_nowait(msg)
        except queue.Full:
            # 背压：丢掉最旧的一条，腾位给最新态势。
            try:
                self._outbox.get_nowait()
            except queue.Empty:
                pass
            try:
                self._outbox.put_nowait(msg)
            except queue.Full:
                pass

    # -----------------------------------------------------------------
    # 异步广播任务（事件循环）
    # -----------------------------------------------------------------
    async def _broadcaster(self) -> None:
        """持续从 outbox 取消息并扇出给所有 WebSocket 客户端。"""
        loop = asyncio.get_running_loop()
        try:
            while self._running:
                # 在执行器线程里阻塞等待出队，避免占用事件循环；带超时以便响应关闭。
                try:
                    msg = await loop.run_in_executor(None, self._outbox.get, True, 0.5)
                except queue.Empty:
                    continue
                await self._broadcast(msg)
        except asyncio.CancelledError:  # pragma: no cover - 正常关闭路径
            pass

    async def _broadcast(self, msg: str) -> None:
        """把单条消息发给所有客户端，剔除发送失败（已断开）的连接。"""
        if not self._clients:
            return
        dead: List[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:  # noqa: BLE001  发送失败即视为断开
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    # -----------------------------------------------------------------
    # 便捷启动（阻塞）：供 main_demo 使用
    # -----------------------------------------------------------------
    def run(self) -> None:
        """以 uvicorn 阻塞运行本服务（信号处理交由 uvicorn）。"""
        import uvicorn
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="info")
