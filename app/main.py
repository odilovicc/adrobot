from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.deps import templates
from app.errors import DomainError
from app.keitaro import KeitaroError
from app.routers import api, pages


def create_app() -> FastAPI:
    app = FastAPI(title="Adrobot")
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(pages.router)
    app.include_router(api.router)

    def _is_page(request: Request) -> bool:
        return not request.url.path.startswith("/api/")

    def _page(request: Request, status: int, title: str, message: str) -> Response:
        return templates.TemplateResponse(
            request, "error.html", {"status": status, "title": title, "error": message}, status_code=status
        )

    @app.exception_handler(DomainError)
    def _domain(request: Request, exc: DomainError) -> Response:
        if _is_page(request):
            return _page(request, exc.status_code, "Не получилось", exc.message)
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    @app.exception_handler(KeitaroError)
    def _keitaro(request: Request, exc: KeitaroError) -> Response:
        if _is_page(request):
            if exc.status_code == 404:
                return _page(request, 404, "Не найдено в Keitaro",
                             "Такой кампании нет в Keitaro — возможно, её удалили. Вернитесь к списку.")  # fmt: skip
            return _page(request, 502, "Keitaro не ответил", exc.message)
        return JSONResponse({"error": exc.message}, status_code=502)

    @app.exception_handler(RequestValidationError)
    def _validation(request: Request, exc: RequestValidationError) -> Response:
        first = exc.errors()[0] if exc.errors() else {}
        loc = [str(p) for p in first.get("loc", [])]
        if _is_page(request) and loc[:1] == ["path"]:
            return _page(request, 404, "Страница не найдена", "Такого адреса нет. Проверьте ссылку или вернитесь к списку кампаний.")
        field = ".".join(loc[1:])
        if not field:
            return JSONResponse({"error": "Пустой или некорректный запрос"}, status_code=400)
        return JSONResponse({"error": f"Некорректное значение {field}: {first.get('msg', '')}"}, status_code=400)

    @app.exception_handler(StarletteHTTPException)
    def _http(request: Request, exc: StarletteHTTPException) -> Response:
        if _is_page(request) and exc.status_code == 404:
            return _page(request, 404, "Страница не найдена", "Такого адреса нет. Проверьте ссылку или вернитесь к списку кампаний.")
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    return app


app = create_app()
