import json

def load_json(path):
    with open(f'{path}') as f:
        trelloConfig = json.load(f)
    return trelloConfig

trelloConfig = load_json("trelloConfig.json")
