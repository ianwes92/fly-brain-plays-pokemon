"""A whole-brain Drosophila simulation that runs on a Raspberry Pi 5.

    from flybrain.connectome import Connectome
    from flybrain.engine import Brain

    cx = Connectome()
    brain = Brain(cx)
    brain.drive(cx.select(cell_type="R1-6"), hz=100)
    spiked = brain.step()
"""
__version__ = "0.1.0"
