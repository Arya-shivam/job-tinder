import asyncio

def pytest_asyncio_loop_factories(config, item):
    # Psycopg async connections require the selector loop on Windows; use it everywhere.
    return {"selector": asyncio.SelectorEventLoop}
