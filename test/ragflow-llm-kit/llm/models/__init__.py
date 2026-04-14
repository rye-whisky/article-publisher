"""
Auto-discovery plugin registration mechanism.
Each model class uses _FACTORY_NAME to register itself into global dictionaries.
Usage:
    from llm.models import ChatModel, EmbeddingModel, RerankModel
    model = ChatModel["OpenAI"](api_key, model_name, base_url=base_url)
"""
import importlib
import inspect

ChatModel = {}
EmbeddingModel = {}
RerankModel = {}

MODULE_MAPPING = {
    "chat_model": ChatModel,
    "embedding_model": EmbeddingModel,
    "rerank_model": RerankModel,
}

package_name = __name__

for module_name, mapping_dict in MODULE_MAPPING.items():
    full_module_name = f"{package_name}.{module_name}"
    try:
        module = importlib.import_module(full_module_name)
    except ImportError:
        continue

    base_class = None
    for name, obj in inspect.getmembers(module):
        if inspect.isclass(obj) and name == "Base":
            base_class = obj
            break
    if base_class is None:
        continue

    for _, obj in inspect.getmembers(module):
        if inspect.isclass(obj) and issubclass(obj, base_class) and obj is not base_class and hasattr(obj, "_FACTORY_NAME"):
            if isinstance(obj._FACTORY_NAME, list):
                for factory_name in obj._FACTORY_NAME:
                    mapping_dict[factory_name] = obj
            else:
                mapping_dict[obj._FACTORY_NAME] = obj

__all__ = ["ChatModel", "EmbeddingModel", "RerankModel"]
