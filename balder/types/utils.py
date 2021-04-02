

def camelCase(st):
    output = ''.join(x for x in st if not x == "_")
    return output[0].lower() + output[1:]


def classToString(cls):
    return camelCase(cls.__name__)