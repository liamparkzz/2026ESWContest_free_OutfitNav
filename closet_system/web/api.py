from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ai_proxy import analyze_external


class RegisterBody(BaseModel):
    rfid: str
    category: str = ""
    color: str = ""
    season: str = ""
    extra: str = ""
    position: str = ""
    color_hex: str = ""
    pattern: str = ""


class AutomaticRegistrationBody(BaseModel):
    category: str = ""
    color: str = ""
    season: str = ""
    extra: str = ""
    position: str = ""
    color_hex: str = ""
    pattern: str = ""


class FindBody(BaseModel):
    query: str


class CodiRecommendationBody(BaseModel):
    location: str
    top_n: int = 3


class CodiActivateBody(BaseModel):
    top_id: int
    bottom_id: int


class CodiPreferenceBody(BaseModel):
    item_id: int
    preference: int


def create_app(controller, cfg: dict) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        controller.start()
        yield
        controller.stop()

    app = FastAPI(title="Smart Closet Integrated", lifespan=lifespan)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def home():
        return FileResponse(
            static_dir / "index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @app.post("/api/analyze")
    async def analyze(file: UploadFile = File(...)):
        return await analyze_external(file, cfg.get("ai", {}) or {})

    @app.post("/api/scan-rfid")
    def scan_rfid():
        try:
            return controller.scan_unregistered_rfid()
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.post("/api/register")
    def register(body: RegisterBody):
        try:
            return controller.register_item(
                rfid=body.rfid,
                category=body.category,
                color=body.color,
                season=body.season,
                extra=body.extra,
                position=body.position,
                color_hex=body.color_hex,
                pattern=body.pattern,
            )
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.post("/api/registration/start")
    def start_registration(body: AutomaticRegistrationBody):
        try:
            return controller.start_automatic_registration(
                category=body.category,
                color=body.color,
                season=body.season,
                extra=body.extra,
                position=body.position,
                color_hex=body.color_hex,
                pattern=body.pattern,
            )
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/api/registration/status")
    def registration_status():
        return controller.registration_status()

    @app.post("/api/registration/cancel")
    def cancel_registration():
        return controller.cancel_automatic_registration()

    @app.get("/api/clothes/state")
    def clothes_state():
        try:
            return controller.verify_closet_state(max_age_s=0.5, force=True)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/clothes/inside")
    def clothes_inside():
        try:
            return controller.list_inside_items()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/clothes/registered")
    def clothes_registered():
        try:
            return controller.list_registered_items()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.delete("/api/clothes/{item_id}")
    def delete_clothing(item_id: int):
        try:
            result = controller.delete_registered_item(item_id)
            if not result.get("ok"):
                raise HTTPException(status_code=404, detail=result.get("message", "등록된 옷을 찾지 못했습니다."))
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.post("/api/find")
    def find(body: FindBody):
        try:
            return controller.find(body.query)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/find/complete")
    def find_complete():
        try:
            return controller.complete_find()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.post("/api/codi/recommendations")
    def codi_recommendations(body: CodiRecommendationBody):
        try:
            return controller.recommend_codi(body.location, top_n=body.top_n)
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.post("/api/codi/activate")
    def codi_activate(body: CodiActivateBody):
        try:
            return controller.activate_codi(body.top_id, body.bottom_id)
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.post("/api/codi/preference")
    def codi_preference(body: CodiPreferenceBody):
        try:
            result = controller.update_codi_preference(body.item_id, body.preference)
            if not result.get("ok"):
                raise HTTPException(status_code=404, detail=result.get("message"))
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/api/status")
    def status():
        return controller.status()

    @app.get("/api/events")
    def events(limit: int = 50):
        return {"events": controller.db.recent_events(limit=max(1, min(limit, 500)))}

    return app
