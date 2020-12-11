from yacs.config import CfgNode as CN

_C = CN()
_C.data = CN(
    dict(
        batch_size=8, # batch size
        valid_size=64, # the first N examples are reserved for validation
        sample_rate=22050, # Hz, sample rate
        n_fft=1024, # fft frame size
        win_length=1024, # window size
        hop_length=256,  # hop size between ajacent frame
        f_max=8000, # Hz, max frequency when converting to mel
        n_mels=80,  # mel bands
        clip_frames=65, # mel clip frames
    )
)

_C.model = CN(
    dict(
        upsample_factors=[16, 16],
        n_flows=8, # number of flows in WaveFlow
        n_layers=8, # number of conv block in each flow
        n_group=16, # folding factor of audio and spectrogram
        channels=128, # resiaudal channel in each flow
        kernel_size=[3, 3], # kernel size in each conv block
        sigma=1.0, # stddev of the random noise
    )
)

_C.training = CN(
    dict(
        lr=1e-4, # learning rates
        valid_interval=1000, # validation
        save_interval=10000, # checkpoint
        max_iteration=3000000, # max iteration to train
    )
)

def get_cfg_defaults():
    """Get a yacs CfgNode object with default values for my_project."""
    # Return a clone so that the defaults will not be altered
    # This is for the "local variable" use pattern
    return _C.clone()
