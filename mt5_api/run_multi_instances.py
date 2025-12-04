# run_multi_instances.py
import asyncio
import uvicorn

PROJECT_APP = "main:app"  # <-- punta a mt5Manager_BE\main.py

PORTS = [8000, 8001, 8002, 8003]

async def main():
    servers = []
    for port in PORTS:
        config = uvicorn.Config(
            PROJECT_APP,
            host="0.0.0.0",
            port=port,
            log_level="info"
        )
        server = uvicorn.Server(config)
        servers.append(server.serve())

    await asyncio.gather(*servers)

if __name__ == "__main__":
    asyncio.run(main())
