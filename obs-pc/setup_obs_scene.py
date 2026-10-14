#!/usr/bin/env python3
"""Create the stream's OBS scene in one go (OBS 30+ with obs-websocket enabled; its password is read from OBS's own config).

    python setup_obs_scene.py

Builds the scene named in config.json ("obs_scene", default "Fly Brain v2") at 1920x1080:

    Game      browser source  http://<pi_host>:8765/game   0,0     1152x768  (the bare GBA frame)
    Brainviz  browser source  http://127.0.0.1:8777/brainviz  1152,0  768x670   (the live brain)
    HUD       browser source  http://127.0.0.1:8777/hud    0,0     1920x1080 (everything else)

The panel (flypanel.py) must be running for Brainviz and HUD to load. Alert widgets
(StreamElements etc.) are yours to add on top. Re-running is safe: existing inputs are
reused and only their settings/positions are updated.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import obsws
from config import CFG

LAYERS = [
    # name, url, width, height, x, y
    ('Game', 'http://%s:8765/game' % CFG['pi_host'], 1152, 768, 0, 0),
    ('Brainviz', 'http://127.0.0.1:%d/brainviz' % int(CFG['panel_port']), 768, 670, 1152, 0),
    ('HUD', 'http://127.0.0.1:%d/hud' % int(CFG['panel_port']), 1920, 1080, 0, 0),
]


def main():
    o = obsws.connect()
    v = o.call('GetVideoSettings')
    if (v['baseWidth'], v['baseHeight']) != (1920, 1080):
        print('warning: OBS canvas is %sx%s; the layout is designed for 1920x1080' % (v['baseWidth'], v['baseHeight']))
    scene = CFG['obs_scene']
    if scene not in [s['sceneName'] for s in o.call('GetSceneList')['scenes']]:
        o.call('CreateScene', {'sceneName': scene})
    inputs = {i['inputName'] for i in o.call('GetInputList')['inputs']}
    for name, url, w, h, x, y in LAYERS:
        settings = {'url': url, 'width': w, 'height': h, 'fps': 30, 'reroute_audio': False, 'shutdown': False}
        if name in inputs:
            o.call('SetInputSettings', {'inputName': name, 'inputSettings': settings})
        else:
            o.call('CreateInput', {'sceneName': scene, 'inputName': name, 'inputKind': 'browser_source',
                                   'inputSettings': settings, 'sceneItemEnabled': True})
    items = {i['sourceName']: i['sceneItemId'] for i in o.call('GetSceneItemList', {'sceneName': scene})['sceneItems']}
    for idx, (name, url, w, h, x, y) in enumerate(LAYERS):
        iid = items.get(name) or o.call('CreateSceneItem', {'sceneName': scene, 'sourceName': name})['sceneItemId']
        o.call('SetSceneItemTransform', {'sceneName': scene, 'sceneItemId': iid,
                                         'sceneItemTransform': {'positionX': x, 'positionY': y, 'scaleX': 1, 'scaleY': 1}})
        o.call('SetSceneItemIndex', {'sceneName': scene, 'sceneItemId': iid, 'sceneItemIndex': idx})
    print('scene "%s" ready: %s' % (scene, ', '.join(l[0] for l in LAYERS)))
    print('make it the program scene in OBS, then add your alert widgets on top.')


if __name__ == '__main__':
    main()
