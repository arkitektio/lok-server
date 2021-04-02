from django.db.models import Model
from pydantic import BaseModel
from django.core import serializers
class PayloadSerializer:

    @classmethod
    def model_serializer(cls, unpacked):
        return serializers.serialize("json", unpacked, fields=('id'))

    @classmethod
    def model_deserializer(cls, packed):
        for obj in serializers.deserialize('json', packed):
            return obj.object
    

    @classmethod
    def pack(cls, unpacked):
        if isinstance(unpacked, BaseModel):
            return {"type": "pydantic", "payload": unpacked.dict()}
        if isinstance(unpacked, Model):
            return {"type": "model", "payload": cls.model_serializer([unpacked])}
        if isinstance(unpacked, dict):
            return {"type": "dict", "payload": unpacked}
        
        return {"type": "scalar", "payload": unpacked}



    @classmethod
    def unpack(cls, packed, basemodel=None):
        print(packed)
        type = packed["type"]
        payload = packed["payload"]
        if type == "model": 
            return cls.model_deserializer(payload)
        if type == "dict":
            return payload
        if type == "pydantic":
            assert basemodel is not None, "Please provide a basemodel in your unpack"
            return basemodel(**payload)
        if type == "scalar":
            return payload
        
        raise NotImplementedError(f"Got unknown type {type}")
