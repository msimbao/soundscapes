
from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.image import Image, AsyncImage
from kivy.properties import ObjectProperty


import os
import pickle
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import make_pipeline

from tqdm import tqdm
import json
from collections import Counter
from access_points import get_scanner


# ============================================================================

# Part for Finding Locations with ML

# ============================================================================

class LearnLocation(Exception):
    pass


def get_pipeline(clf=RandomForestClassifier(n_estimators=100, class_weight="balanced")):
    return make_pipeline(DictVectorizer(sparse=False), clf)


def train_model(path=None):
    model_file = get_model_file(path)
    X, y = get_train_data(path)
    if len(X) == 0:
        raise ValueError("No wifi access points have been found during training")
    # fantastic: because using "quality" rather than "rssi", we expect values 0-150
    # 0 essentially indicates no connection
    # 150 is something like best possible connection
    # Not observing a wifi will mean a value of 0, which is the perfect default.
    lp = get_pipeline()
    lp.fit(X, y)
    with open(model_file, "wb") as f:
        pickle.dump(lp, f)
    return lp


def get_model(path=None):
    model_file = get_model_file(path)
    if not os.path.isfile(model_file):  # pragma: no cover
        msg = "First learn a location, e.g. with `whereami learn -l kitchen`."
        raise LearnLocation(msg)
    with open(model_file, "rb") as f:
        lp = pickle.load(f)
    return lp

def predict_proba(input_path=None, model_path=None, device=""):
    lp = get_model(model_path)
    data_sample = sample(device) if input_path is None else get_external_sample(input_path)
    print(json.dumps(dict(zip(lp.classes_, lp.predict_proba(data_sample)[0]))))


def predict(input_path=None, model_path=None, device=""):
    lp = get_model(model_path)
    data_sample = sample(device) if input_path is None else get_external_sample(input_path)
    return lp.predict(data_sample)[0]


def crossval(clf=None, X=None, y=None, folds=10, n=5, path=None):
    if X is None or y is None:
        X, y = get_train_data(path)
    if len(X) < folds:
        raise ValueError('There are not enough samples ({}). Need at least {}.'.format(len(X), folds))
    clf = clf or get_model(path)
    tot = 0
    print("KFold folds={}, running {} times".format(folds, n))
    for i in range(n):
        res = cross_val_score(clf, X, y, cv=folds).mean()
        tot += res
        print("{}/{}: {}".format(i + 1, n, res))
    print("-------- total --------")
    print(tot / n)
    return tot / n


def locations(path=None):
    _, y = get_train_data(path)
    if len(y) == 0:  # pragma: no cover
        msg = "No location samples available. First learn a location, e.g. with `whereami learn -l kitchen`."
        print(msg)
    else:
        occurrences = Counter(y)
        for key, value in occurrences.items():
            print("{}: {}".format(key, value))


class Predicter():
    def __init__(self, model=None, device=""):
        self.model = model
        self.device = device
        self.clf = get_model(model)
        self.wifi_scanner = get_scanner(device)
        self.predicted_value = None

    def predict(self):
        aps = self.wifi_scanner.get_access_points()
        self.predicted_value = self.clf.predict(aps_to_dict(aps))[0]
        return self.predicted_value

    def refresh(self):
        self.clf = get_model(self.model)
        self.wifi_scanner = get_scanner(self.device)

def get_whereami_path(path=None):
    if path is None:
        _USERNAME = os.getenv("") or os.getenv("") or ""
        path = os.path.expanduser('' + _USERNAME)
        path = os.path.join(path, "model")
    return os.path.expanduser(path)


def ensure_whereami_path():
    path = get_whereami_path()
    if not os.path.exists(path):  # pragma: no cover
        os.makedirs(path)
    return path


def get_model_file(path=None, model="model.pkl"):
    path = ensure_whereami_path() if path is None else path
    return os.path.join(path, model)


def get_label_file(path, label):
    return os.path.join(get_whereami_path(path), label)


def rename_label(label, new_label, path=None):
    path = ensure_whereami_path() if path is None else path
    from_path = os.path.join(path, label + ".txt")
    new_path = os.path.join(path, new_label + ".txt")
    os.rename(from_path, new_path)
    print("Renamed {} to {}".format(from_path, new_path))

def write_data(label_path, data):
    with open(label_path, "a") as f:
        f.write(json.dumps(data))
        f.write("\n")


def learn(label, n=1, device=""):
    path = ensure_whereami_path()
    label_path = get_label_file(path, label + ".txt")
    for i in tqdm(range(n)):
        if i != 0:
            time.sleep(15)
        try:
            new_sample = sample(device)
            if new_sample:
                write_data(label_path, new_sample)
        except KeyboardInterrupt:  # pragma: no cover
            break
    train_model()

def aps_to_dict(aps):
    return {ap['ssid'] + " " + ap['bssid']: ap['quality'] for ap in aps}


def sample(device=""):
    wifi_scanner = get_scanner(device)
    if not os.environ.get("PYTHON_ENV", False):
        aps = wifi_scanner.get_access_points()
    else:
        aps = [{"quality": 100, "bssid": "XX:XX:XX:XX:XX:84",
                "ssid": "X", "security": "XX"}]
    return aps_to_dict(aps)


def get_external_sample(path):
    data = []
    with open(os.path.join(path, "current.loc.txt")) as f:
        for line in f:
            data.append(json.loads(line))
    return data


def get_train_data(folder=None):
    if folder is None:
        folder = ensure_whereami_path()
    X = []
    y = []
    for fname in os.listdir(folder):
        if fname.endswith(".txt"):
            data = []
            with open(os.path.join(folder, fname)) as f:
                for line in f:
                    data.append(json.loads(line))
            X.extend(data)
            y.extend([fname.rstrip(".txt")] * len(data))
    return X, y

#======================================================

# Create both screens. Please note the root.manager.current: this is how
# you can control the ScreenManager from kv. Each screen has by default a
# property manager that gives you the instance of the ScreenManager used.

Builder.load_string("""
<MenuScreen>:
    BoxLayout:
        Button:
            text: 'Goto settings'
            on_press: root.manager.current = 'settings'
            size_hint:0.1,0.1
        AsyncImage:
            source: 'https://i.stack.imgur.com/sJAwu.png'
            size: self.texture_size

<SettingsScreen>:

    test:test

    BoxLayout:
        Button:
            text: 'My settings button'
        Button:
            text: 'Back to menu'
            on_press: 
                root.manager.current = 'menu'
                root.my_prediction()
        
        Label:
            id:test
            text: 'Hello'

""")

# Declare both screens
class MenuScreen(Screen):
    pass

class SettingsScreen(Screen):

    # test = ObjectProperty

    # def my_prediction(self):
    #     new = predict()
    #     self.test.text = new
    #     print(new)
    pass

# Create the screen manager
sm = ScreenManager()
sm.add_widget(MenuScreen(name='menu'))
sm.add_widget(SettingsScreen(name='settings'))

class TestApp(App):

    def build(self):
        return sm

if __name__ == '__main__':
    TestApp().run()
