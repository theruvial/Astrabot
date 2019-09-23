import json

def load_json(path):
    with open(f'{path}') as f:
        config = json.load(f)
    return config

config = load_json("config.json")
