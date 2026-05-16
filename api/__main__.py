import argparse

from api.server import create_app
from core.sim import Sim
from engine.engine import SimEngine
from identity import generate_sim_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="sim_v2 API server")
    parser.add_argument("--sims", type=int, default=3)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is required to run api server") from exc

    sims = [Sim(generate_sim_profile()) for _ in range(args.sims)]
    engine = SimEngine(sims=sims)
    app = create_app(engine)
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
