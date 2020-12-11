import time
from collections import defaultdict
import numpy as np

import paddle
from paddle import distributed as dist
from paddle.io import DataLoader, DistributedBatchSampler

import parakeet
from parakeet.data import dataset
from parakeet.frontend import EnglishCharacter
from parakeet.training.cli import default_argument_parser
from parakeet.training.experiment import ExperimentBase
from parakeet.utils import display, mp_tools
from parakeet.models.tacotron2 import Tacotron2, Tacotron2Loss

from config import get_cfg_defaults
from ljspeech import LJSpeech, LJSpeechCollector

class Experiment(ExperimentBase):
    def compute_losses(self, inputs, outputs):
        _, mel_targets, _, _, stop_tokens = inputs

        mel_outputs = outputs["mel_output"]
        mel_outputs_postnet = outputs["mel_outputs_postnet"]
        stop_logits = outputs["stop_logits"]

        losses = self.criterion(
            mel_outputs, mel_outputs_postnet, stop_logits,
                mel_targets, stop_tokens)
        return losses

    def train_batch(self):
        start = time.time()
        batch = self.read_batch()
        data_loader_time = time.time() - start

        self.optimizer.clear_grad()
        self.model.train()
        texts, mels, text_lens, output_lens, stop_tokens = batch
        outputs = self.model(texts, mels, text_lens, output_lens)
        losses = self.compute_losses(batch, outputs)
        loss = losses["loss"]
        loss.backward() 
        self.optimizer.step()
        iteration_time = time.time() - start

        losses_np = {k: float(v) for k, v in losses.items()}
        # logging
        msg = "Rank: {}, ".format(dist.get_rank())
        msg += "step: {}, ".format(self.iteration)
        msg += "time: {:>.3f}s/{:>.3f}s, ".format(data_loader_time, iteration_time)
        msg += ', '.join('{}: {:>.6f}'.format(k, v) for k, v in losses_np.items())
        self.logger.info(msg)
        
        if dist.get_rank() == 0:
            for k, v in losses_np.items():
                self.visualizer.add_scalar(f"train_loss/{k}", v, self.iteration)

    @mp_tools.rank_zero_only
    @paddle.no_grad()
    def valid(self):
        valid_losses = defaultdict(list)
        for i, batch in enumerate(self.valid_loader):
            texts, mels, text_lens, output_lens, stop_tokens = batch
            outputs = self.model(texts, mels, text_lens, output_lens)
            losses = self.compute_losses(batch, outputs)
            for k, v in losses.items():
                valid_losses[k].append(float(v))

            attention_weights = outputs["alignments"]
            display.add_attention_plots(
                self.visualizer, 
                f"valid_sentence_{i}_alignments", 
                attention_weights, 
                self.iteration)

        # write visual log
        valid_losses = {k: np.mean(v) for k, v in valid_losses.items()}

        # logging
        msg = "Valid: "
        msg += "step: {}, ".format(self.iteration)
        msg += ', '.join('{}: {:>.6f}'.format(k, v) for k, v in valid_losses.items())
        self.logger.info(msg)

        for k, v in valid_losses.items():
            self.visualizer.add_scalar(f"valid/{k}", v, self.iteration)

    def setup_model(self):
        config = self.config
        frontend = EnglishCharacter()
        model = Tacotron2(frontend,
                 d_mels=config.data.d_mels,
                 d_encoder=config.model.d_encoder,
                 encoder_conv_layers=config.model.encoder_conv_layers,
                 encoder_kernel_size=config.model.encoder_kernel_size,
                 d_prenet=config.model.d_prenet,
                 d_attention_rnn=config.model.d_attention_rnn,
                 d_decoder_rnn=config.model.d_decoder_rnn,
                 attention_filters=config.model.attention_filters,
                 attention_kernel_size=config.model.attention_kernel_size,
                 d_attention=config.model.d_attention,
                 d_postnet=config.model.d_postnet,
                 postnet_kernel_size=config.model.postnet_kernel_size,
                 postnet_conv_layers=config.model.postnet_conv_layers,
                 reduction_factor=config.model.reduction_factor,
                 p_encoder_dropout=config.model.p_encoder_dropout,
                 p_prenet_dropout=config.model.p_prenet_dropout,
                 p_attention_dropout=config.model.p_attention_dropout,
                 p_decoder_dropout=config.model.p_decoder_dropout,
                 p_postnet_dropout=config.model.p_postnet_dropout)

        if self.parallel:
            model = paddle.DataParallel(model)

        grad_clip = paddle.nn.ClipGradByGlobalNorm(config.training.grad_clip_thresh)
        optimizer = paddle.optimizer.Adam(
            learning_rate=config.training.lr,
            parameters=model.parameters(),
            weight_decay=paddle.regularizer.L2Decay(config.training.weight_decay),
            grad_clip=grad_clip)
        criterion = Tacotron2Loss()
        self.model = model
        self.optimizer = optimizer
        self.criterion = criterion


    def setup_dataloader(self):
        args = self.args
        config = self.config
        ljspeech_dataset = LJSpeech(args.data)

        valid_set, train_set = dataset.split(ljspeech_dataset, config.data.valid_size)
        batch_fn = LJSpeechCollector(padding_idx=config.data.padding_idx)

        if not self.parallel:
            self.train_loader = DataLoader(
                train_set, 
                batch_size=config.data.batch_size, 
                shuffle=True, 
                drop_last=True,
                collate_fn=batch_fn)
        else:
            sampler = DistributedBatchSampler(
                train_set, 
                batch_size=config.data.batch_size,
                shuffle=True,
                drop_last=True)
            self.train_loader = DataLoader(
                train_set, batch_sampler=sampler, collate_fn=batch_fn)

        self.valid_loader = DataLoader(
            valid_set,
            batch_size=config.data.batch_size,
            shuffle=False,
            drop_last=False,
            collate_fn=batch_fn)



def main_sp(config, args):
    exp = Experiment(config, args)
    exp.setup()
    exp.run()

def main(config, args):
    if args.nprocs > 1 and args.device == "gpu":
        dist.spawn(main_sp, args=(config, args), nprocs=args.nprocs)
    else:
        main_sp(config, args)

if __name__ == "__main__":
    config = get_cfg_defaults()
    parser = default_argument_parser()
    args = parser.parse_args()
    if args.config: 
        config.merge_from_file(args.config)
    if args.opts:
        config.merge_from_list(args.opts)
    config.freeze()
    print(config)
    print(args)

    main(config, args)