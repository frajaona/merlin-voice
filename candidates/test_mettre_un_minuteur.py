import asyncio
import importlib.util
import sys
from pathlib import Path

# Pour mocker les types de pipecat sans nécessiter toute la librairie si besoin,
# mais on suppose qu'ils sont installés via le venv (comme requis par l'import du plugin).

class MockLLM:
    def __init__(self):
        self.frames = []
    
    async def push_frame(self, frame):
        self.frames.append(frame)

class MockFunctionCallParams:
    def __init__(self, arguments):
        self.arguments = arguments
        self.llm = MockLLM()
        self.callbacks = []
    
    async def result_callback(self, result):
        self.callbacks.append(result)

async def run_test():
    # 1. Importer le plugin via importlib depuis le fichier local
    plugin_path = Path(__file__).parent / "mettre_un_minuteur.py"
    spec = importlib.util.spec_from_file_location("mettre_un_minuteur", plugin_path)
    plugin = importlib.util.module_from_spec(spec)
    sys.modules["mettre_un_minuteur"] = plugin
    spec.loader.exec_module(plugin)
    
    # 2. Vérifier SCHEMA
    assert hasattr(plugin, "SCHEMA"), "Le plugin doit exporter SCHEMA"
    # Vérification naïve du type en se basant sur les attributs (nom, etc)
    assert getattr(plugin.SCHEMA, "name", None) == "mettre_un_minuteur", "Le nom du schema doit être 'mettre_un_minuteur'"
    
    # 3. Vérifier que le handler est une coroutine
    assert asyncio.iscoroutinefunction(plugin.handler), "Le handler doit être une coroutine (async def)"
    
    # 4. Appeler handler avec un faux FunctionCallParams
    params = MockFunctionCallParams({"duration_seconds": 600}) # 10 minutes = 600 secondes
    
    await plugin.handler(params)
    
    # 5. Vérifier que result_callback est appelé exactement une fois avec un dict
    assert len(params.callbacks) == 1, f"result_callback a été appelé {len(params.callbacks)} fois au lieu de 1."
    assert isinstance(params.callbacks[0], dict), "Le paramètre du result_callback doit être un dictionnaire (dict)."
    
    print("Test passed successfully.")
    
    # Nettoyage de la tâche d'arrière-plan pour éviter les warnings à la fin du run_test
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()

if __name__ == "__main__":
    asyncio.run(run_test())
    sys.exit(0)
