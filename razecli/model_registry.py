"""Dynamic model registry for modular device support."""

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import razecli.models
from razecli.models.base import ModelSpec


@dataclass
class ModelRegistry:
    _models: Dict[str, ModelSpec]

    @classmethod
    def load(cls) -> "ModelRegistry":
        models: Dict[str, ModelSpec] = {}
        candidate_modules: set[str] = set()

        for module_info in pkgutil.iter_modules(razecli.models.__path__):
            module_name = module_info.name
            if module_name.startswith("_") or module_name == "base":
                continue
            candidate_modules.add(module_name)

        candidate_modules.update(
            getattr(razecli.models, "BUILTIN_MODEL_MODULES", ())
        )

        for module_name in sorted(candidate_modules):
            try:
                module = importlib.import_module(
                    f"{razecli.models.__name__}.{module_name}"
                )
            except ModuleNotFoundError:
                continue
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

    def default_cli_model_slug(self) -> Optional[str]:
        defaults = [model for model in self.list() if bool(getattr(model, "cli_default_target", False))]
        if defaults:
            return defaults[0].slug
        models = self.list()
        return models[0].slug if models else None

    def ble_endpoint_product_ids(self) -> Tuple[int, ...]:
        pids = set()
        for model in self.iter():
            for item in tuple(getattr(model, "ble_endpoint_product_ids", ()) or ()):
                try:
                    pid = int(item)
                except Exception:
                    continue
                if 0 <= pid <= 0xFFFF:
                    pids.add(pid)
        return tuple(sorted(pids))
