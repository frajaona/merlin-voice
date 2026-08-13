import asyncio
import importlib
import sys
import os
from unittest.mock import AsyncMock, MagicMock

# Ensure the root of the repository is in the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pipecat.services.llm_service import FunctionCallParams
from pipecat.adapters.schemas.function_schema import FunctionSchema

async def main():
    try:
        plugin = importlib.import_module("candidates.voir_la_meteo_d")
    except Exception as e:
        print(f"Failed to import plugin: {e}")
        sys.exit(1)
        
    if not hasattr(plugin, "SCHEMA"):
        print("SCHEMA is missing")
        sys.exit(1)
        
    if not hasattr(plugin, "handler"):
        print("handler is missing")
        sys.exit(1)
        
    if not isinstance(plugin.SCHEMA, FunctionSchema):
        print("SCHEMA must be a FunctionSchema")
        sys.exit(1)
        
    if plugin.SCHEMA.name != "voir_la_meteo_d":
        print("SCHEMA.name must be 'voir_la_meteo_d'")
        sys.exit(1)
        
    if not asyncio.iscoroutinefunction(plugin.handler):
        print("handler must be a coroutine")
        sys.exit(1)
        
    mock_llm = MagicMock()
    mock_llm.push_frame = AsyncMock()
    
    mock_result_callback = AsyncMock()
    
    params = FunctionCallParams(
        function_name="voir_la_meteo_d",
        arguments={"lieu": "Paris", "date": "demain"},
        result_callback=mock_result_callback,
        llm=mock_llm,
        tool_call_id="call_123",
        pipeline_worker=MagicMock(),
        context=MagicMock()
    )
    
    await plugin.handler(params)
    
    if mock_result_callback.call_count != 1:
        print(f"result_callback was called {mock_result_callback.call_count} times, expected exactly 1")
        sys.exit(1)
        
    call_args = mock_result_callback.call_args[0]
    if not isinstance(call_args[0], dict):
        print("result_callback must be called with a dict")
        sys.exit(1)
        
    print("Test passed.")
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
