"""Dynamic model registry for modular device support."""

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import razecli.models
from razecli.models.base import ModelSpec


@dataclass
class ModelRegistry:
    _models: Dict[str, ModelSpec]

    @classmethod
    def load(cls) -> "ModelRegistry":
        models: Dict[str, ModelSpec] = {}

        for module_info in pkgutil.iter_modules(razecli.models.__path__):
            module_name = module_info.name
            if module_name.startswith("_") or module_name == "base":
                continue

            module = importlib.import_module(f"{razecli.models.__name__}.{module_name}")
            model = getattr(module, "MODEL", None)
            if isinstance(model, ModelSpec):
                models[model.slug] = model

        return cls(models)

    def list(self) -> List[ModelSpec]:
        return sorted(self._models.values(), key=lambda model: model.slug)

    def get(self, slug: str) -> Optional[ModelSpec]:
        return self._models.get(slug)

    def find_by_usb(self, vendor_id: int, product_id: int) -> Optional[ModelSpec]:
        for model in self._models.values():
            if model.matches(vendor_id, product_id):
                return model
        return None

    def find_by_name(self, device_name: str) -> Optional[ModelSpec]:
        for model in self._models.values():
            if model.matches_name(device_name):
                return model
        return None

    def iter(self) -> Iterable[ModelSpec]:
        return self._models.values()
