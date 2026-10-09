"""Shared model API for desktop Pydantic 2 and Android Pydantic 1."""
import json
from typing import get_origin

from pydantic import BaseModel as PydanticBaseModel, Field as PydanticField, HttpUrl
from pydantic.fields import FieldInfo


def Field(*args, **kwargs):
    """Keep length validation on collection defaults on both supported versions."""
    if hasattr(PydanticBaseModel, "model_dump"):
        for name in ("min", "max"):
            items = kwargs.pop(name + "_items", None)
            if items is not None:
                kwargs.setdefault(name + "_length", items)
    else:
        collection = kwargs.get("default_factory") in (list, tuple, set) or (
            args and isinstance(args[0], (list, tuple, set))
        )
        if collection:
            for name in ("min", "max"):
                length = kwargs.pop(name + "_length", None)
                if length is not None:
                    kwargs[name + "_items"] = length
    return PydanticField(*args, **kwargs)


class LegacyModelMeta(type(PydanticBaseModel)):
    def __new__(mcs, name, bases, namespace, **kwargs):
        if not hasattr(PydanticBaseModel, "model_dump"):
            for field_name, annotation in namespace.get("__annotations__", {}).items():
                origin = get_origin(annotation)
                collection = origin in (list, tuple, set) or (
                    isinstance(annotation, str) and annotation.startswith(("list[", "tuple[", "set[", "List["))
                )
                info = namespace.get(field_name)
                if collection and isinstance(info, FieldInfo):
                    for prefix in ("min", "max"):
                        length = getattr(info, prefix + "_length", None)
                        if length is not None:
                            setattr(info, prefix + "_items", length)
                            setattr(info, prefix + "_length", None)
        return super().__new__(mcs, name, bases, namespace, **kwargs)


class LegacyBaseModel(PydanticBaseModel, metaclass=LegacyModelMeta):
    def model_dump(self, *, mode="python", **kwargs):
        if mode == "json":
            return json.loads(self.json(**kwargs))
        return self.dict(**kwargs)

    def model_copy(self, **kwargs):
        return self.copy(**kwargs)

    @classmethod
    def model_validate(cls, value, **kwargs):
        return cls.parse_obj(value)

    @classmethod
    def model_validate_json(cls, value, **kwargs):
        return cls.parse_raw(value)

    @classmethod
    def model_json_schema(cls, **kwargs):
        return cls.schema(**kwargs)


BaseModel = PydanticBaseModel if hasattr(PydanticBaseModel, "model_dump") else LegacyBaseModel
