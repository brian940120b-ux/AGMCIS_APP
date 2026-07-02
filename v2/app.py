from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import APP_NAME, VERSION

app = FastAPI(
    title=APP_NAME,
    version=VERSION
)

templates = Jinja2Templates(directory="templates")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health():
    return {
        "app": APP_NAME,
        "version": VERSION,
        "status": "running"
    }
