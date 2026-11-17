from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.errors import DomainError
from app.keitaro import KeitaroError
from app.routers import api, pages


def create_app() -> FastAPI:
    app = FastAPI(title="Adrobot")
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(pages.router)
    app.include_router(api.router)

    @app.exception_handler(DomainError)
    def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    @app.exception_handler(KeitaroError)
    def _keitaro(_: Request, exc: KeitaroError) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=502)

    @app.exception_handler(RequestValidationError)
    def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:])
        return JSONResponse({"error": f"Некорректное значение {field}: {first.get('msg', '')}"}, status_code=400)

    return app


app = create_app()
