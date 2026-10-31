import importlib.util, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nse-common"))

def load(skill, filename):
    path = os.path.join(ROOT, skill, filename)
    spec = importlib.util.spec_from_file_location(filename[:-3] + "_" + skill.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
