from engine.engine import SimEngine

try:
    from fastapi import FastAPI

    FASTAPI_OK = True
except ImportError:
    FASTAPI_OK = False
    FastAPI = None


def create_app(engine: SimEngine):
    if not FASTAPI_OK:
        raise RuntimeError("fastapi is not installed")
    app = FastAPI(title="sim_v2 API")

    @app.get("/state")
    def state():
        return engine.get_state()

    @app.post("/tick")
    def tick():
        engine.run_tick()
        return engine.get_state()

    return app
