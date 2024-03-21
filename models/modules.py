# -*- coding: utf-8 -*-
# @Time    : 2023/3/21 16:29
# @Author  : Karry Ren

""" The modules for UML_Net.

All modules can be divided into 2 classes:
    - The modules for UML_Net.
        1. PairWiseFeatureMixer(). The feature mixer module.
    - The assisted modules.
        1. PolarizedSelfAttention(). The PolarizedSelfAttention module for PairWiseFeatureMixer.
        2. BasicConv2d(). The basic 2d Conv layer for easy using.

"""

from typing import List
import torch
import torch.nn as nn
import torch.functional as F


# ************************************************************************************ #
# ***************************** THE MODULES FOR UML_NET ****************************** #
# ************************************************************************************ #
class PairWiseFeatureMixer(nn.Module):
    """ The module which mix features in [cls feature list] and [seg feature list]
        to [mutual feature list] by Pairwise Channel Map Interaction.

        Ref. https://www.sciencedirect.com/science/article/abs/pii/S1568494622006184

    """

    def __init__(self):
        """ Init function of PairWiseFeatureMixer.
            For simplification, we just fix the hyperparameters in this model.

        """

        super(PairWiseFeatureMixer, self).__init__()

        # 4 groups
        self.group = 4

        # conv layers for cls_features in cls_feature_list
        self.cf_conv1 = BasicConv2d(64, 256, kernel_size=1)
        self.cf_conv2 = BasicConv2d(256, 256, kernel_size=1)
        self.cf_conv3 = BasicConv2d(512, 256, kernel_size=1)
        self.cf_conv4 = BasicConv2d(1024, 256, kernel_size=1)

        # conv layers for seg_features in seg_feature_list
        self.sf_conv1 = BasicConv2d(64, 256, kernel_size=1)
        self.sf_conv2 = BasicConv2d(256, 256, kernel_size=1)
        self.sf_conv3 = BasicConv2d(512, 256, kernel_size=1)
        self.sf_conv4 = BasicConv2d(1024, 256, kernel_size=1)

        # attention module to strength feature
        self.PSA1 = PolarizedSelfAttention(512)
        self.PSA2 = PolarizedSelfAttention(512)
        self.PSA3 = PolarizedSelfAttention(512)
        self.PSA4 = PolarizedSelfAttention(512)

        # the output conv layers to adjust channels
        self.out_conv1 = BasicConv2d(512, 64, kernel_size=1)
        self.out_conv2 = BasicConv2d(512, 256, kernel_size=1)
        self.out_conv3 = BasicConv2d(512, 512, kernel_size=1)
        self.out_conv4 = BasicConv2d(512, 1024, kernel_size=1)

    def forward(self, cls_feature_list: List[torch.Tensor], seg_feature_list: List[torch.Tensor]):
        """ Forward function of PairWiseFeatureMixer.

        :param: cls_feature_list: feature list of classification encoder
        :param: seg_feature_list: feature list of segmentation encoder

        Because the encoders of classification and segmentation are both Res2Net. The feature_list
         of classification and segmentation have the same shape items:
            - item_0, shape=(bs, 64, h, w)
            - item_1, shape=(bs, 256, h/2, w/2)
            - item_2, shape=(bs, 521, h/4, w/4)
            - item_3, shape=(bs, 1024, h/8, w/8)

        returns:
            - mutual_feature_list: feature list after feature mixing using for mutual learning
                (have the same shape items with cls and seg feature list) !

        """

        # ---- Step 1. Read the cls and seg feature list ---- #
        [cf1, cf2, cf3, cf4] = cls_feature_list
        [sf1, sf2, sf3, sf4] = seg_feature_list

        # ---- Step 2. Conv to set channel to 256 ---- #
        # - conv classification features
        cf1 = self.cf_conv1(cf1)  # channel from 64 => 256
        cf2 = self.cf_conv2(cf2)  # channel from 256 => 256
        cf3 = self.cf_conv3(cf3)  # channel from 512 => 256
        cf4 = self.cf_conv4(cf4)  # channel from 1024 => 256
        # - conv segmentation features
        sf1 = self.sf_conv1(sf1)  # channel from 64 => 256
        sf2 = self.sf_conv2(sf2)  # channel from 256 => 256
        sf3 = self.sf_conv3(sf3)  # channel from 512 => 256
        sf4 = self.sf_conv4(sf4)  # channel from 1024 => 256

        # ---- Step 3. Merge the feature of cls and seg, shuffle to mix ---- #
        merge_feats1 = torch.cat([cf1, sf1], dim=1)
        merge_feats1 = self.channel_shuffle(merge_feats1)  # shape=(bs, 512, h, w)
        merge_feats2 = torch.cat([cf2, sf2], dim=1)
        merge_feats2 = self.channel_shuffle(merge_feats2)  # shape=(bs, 512, h/2, w/2)
        merge_feats3 = torch.cat([cf3, sf3], dim=1)
        merge_feats3 = self.channel_shuffle(merge_feats3)  # shape=(bs, 512, h/4, w/4)
        merge_feats4 = torch.cat([cf4, sf4], dim=1)
        merge_feats4 = self.channel_shuffle(merge_feats4)  # shape=(bs, 512, h/8, w/8)

        # ---- Step 4. PSA and use output conv to adjust channels ---- #
        f1 = self.PSA1(merge_feats1)
        out_f1 = self.out_conv1(f1)  # 512 => 64   out_f1: (bs, 64, h, w)
        f2 = self.PSA2(merge_feats2)
        out_f2 = self.out_conv2(f2)  # 512 => 256  out_f2: (bs, 256, h/2, w/2)
        f3 = self.PSA3(merge_feats3)
        out_f3 = self.out_conv3(f3)  # 512 => 512  out_f3: (bs, 512, h/4, w/4)
        f4 = self.PSA4(merge_feats4)
        out_f4 = self.out_conv4(f4)  # 512 => 1024 out_f4: (bs, 1024, h/8, w/8)

        # ---- Step 5. List the out features and return ---- #
        mutual_feature_list = [out_f1, out_f2, out_f3, out_f4]
        return mutual_feature_list

    def channel_shuffle(self, x: torch.Tensor):
        """ Do the channel shuffle, the core operation of feature mixer.

        :param x: the feature to shuffle.

        """

        # ---- Step 1. Get the shape of feature ---- #
        batch_size, num_channels, height, width = x.data.size()
        assert num_channels % self.group == 0, "Channel shuffle ERROR !!!"

        # ---- Step 2. Use reshape to shuffle (Could be update) ---- #
        group_channels = num_channels // self.group
        x = x.reshape(batch_size, group_channels, self.group, height, width)
        x = x.permute(0, 2, 1, 3, 4)
        x = x.reshape(batch_size, num_channels, height, width)

        return x


class MutualFeatureDecoder(nn.Module):
    """ The `TBraTS-liked` feature decoder of mutual_feature_list. """

    def __init__(self, seg_classes: int):
        """ Init function of MutualFeatureDecoder.

        :param seg_classes: the target classed of segmentation task.

        """

        super(MutualFeatureDecoder, self).__init__()
        self.seg_classes = seg_classes

        #
        self.conv_adj_channel3 = BasicConv2d(1024, 512)
        self.conv_adj_channel2 = BasicConv2d(512, 256)
        self.conv_adj_channel1 = BasicConv2d(256, 128)

        # Backbone Unet decoder structure to decoding features
        self.up_conv3 = UpConv2d(512, 256)
        self.up_conv2 = UpConv2d(256, 128)
        self.up_conv1 = UpConv2d(128, 64)

        # four out conv layers
        self.out_conv4 = nn.Sequential(
            BasicConv2d(512, 256),
            BasicConv2d(256, self.seg_classes)
        )
        self.out_conv3 = nn.Sequential(
            BasicConv2d(256, 128),
            BasicConv2d(128, self.seg_classes)
        )
        self.out_conv2 = nn.Sequential(
            BasicConv2d(128, 64),
            BasicConv2d(64, self.seg_classes)
        )
        self.out_conv1 = nn.Sequential(
            BasicConv2d(64, 32),
            BasicConv2d(32, self.seg_classes)
        )

    def forward(self, mutual_feature_list: List[torch.Tensor]):
        """
        Args:
            mutual_feature_list: input encoding feature (skip connection)
        Returns:
            mutual_evidence: (bs, seg_cls, h, w)
            mutual_alpha: (bs, seg_cls, h, w)
            mutual_uncertainty: (bs, 1, h, w)
            mutual_info_list: [(bs, 64, h, w), (bs, 128, h/2, w/2), (bs, 256, h/4, w/4)]
        """

        mutual_info_list = [None] * len(mutual_feature_list)
        mutual_feature_out_list = [None] * len(mutual_feature_list)

        # ---- Step 0. adjust channel ---- #
        encoding_feature3 = self.conv_adj_channel3(mutual_feature_list[3])
        encoding_feature2 = self.conv_adj_channel2(mutual_feature_list[2])
        encoding_feature1 = self.conv_adj_channel1(mutual_feature_list[1])
        encoding_feature0 = mutual_feature_list[0]

        # ---- Step 1. use Backbone to get final feature ---- #
        x = encoding_feature3  # (bs, 512, h/8, w/8)
        mutual_feature_out_list[3] = self.out_conv4(x)
        x = self.up_conv3(x, encoding_feature2)  # (bs, 256, h/4, w/4)
        mutual_info_list[2] = x
        mutual_feature_out_list[2] = self.out_conv3(x)
        x = self.up_conv2(x, encoding_feature1)  # (bs, 128, h/2, w/2)
        mutual_info_list[1] = x
        mutual_feature_out_list[1] = self.out_conv2(x)
        x = self.up_conv1(x, encoding_feature0)  # (bs, 64, h, w)
        mutual_info_list[0] = x
        mutual_feature_out_list[0] = self.out_conv1(x)

        mutual_evidence_list = [None] * len(mutual_feature_out_list)
        mutual_alpha_list = [None] * len(mutual_feature_out_list)
        mutual_uncertainty_list = [None] * len(mutual_feature_out_list)
        for i in range(len(mutual_feature_out_list)):
            # ---- Step 2. get mutual feature evidence ---- #
            mutual_evidence = self.infer(mutual_feature_out_list[i])  # (bs, c, h, w)
            # ---- Step 3. use dirichlet distribution to get alpha ---- #
            mutual_alpha = mutual_evidence + 1  # dirichlet (bs, c, h, w)
            # ---- Step 4. get belief and uncertainty ---- #
            # based on the evidential theory (b_1 + b_2 + ... b_c + u = 1) #
            S_m = torch.sum(mutual_alpha, dim=1, keepdim=True)  # must keep dim keep it as (bs, 1, h, w) not (bs, h, w)
            mutual_belief = (mutual_alpha - 1) / (S_m.expand(mutual_alpha.shape))  # belief (bs, c, h, w) pixel-wise
            mutual_uncertainty = self.seg_classes / S_m  # uncertainty (bs, 1, h, w) pixel-wise
            # ---- set it ---- #
            mutual_evidence_list[i] = mutual_evidence
            mutual_alpha_list[i] = mutual_alpha
            mutual_uncertainty_list[i] = mutual_uncertainty

        # get eta
        eta = (encoding_feature3, mutual_uncertainty_list[3])

        return mutual_evidence_list, mutual_alpha_list, mutual_uncertainty_list, mutual_info_list, eta

    def infer(self, input):
        evidence = F.softplus(input)
        return evidence


# ************************************************************************************ #
# ******************************* THE ASSISTED MODULES ******************************* #
# ************************************************************************************ #
class PolarizedSelfAttention(nn.Module):
    """ PSAttention algorithm in channel shuffle for PairWiseFeatureMixer,
        use self attention mechanism, we use this as a black box.

        Ref. https://www.sciencedirect.com/science/article/abs/pii/S1568494622006184

    """

    def __init__(self, channel: int = 512):
        """ Init function of PolarizedSelfAttention.

        :param channel: the input channel.

        """

        super(PolarizedSelfAttention, self).__init__()
        self.ch_wv = nn.Conv2d(channel, channel // 2, kernel_size=(1, 1))
        self.ch_wq = nn.Conv2d(channel, 1, kernel_size=(1, 1))
        self.softmax = nn.Softmax(1)
        self.ch_wz = nn.Conv2d(channel // 2, channel, kernel_size=(1, 1))
        self.ln = nn.LayerNorm(channel)
        self.sigmoid = nn.Sigmoid()
        self.sp_wv = nn.Conv2d(channel, channel // 2, kernel_size=(1, 1))
        self.sp_wq = nn.Conv2d(channel, channel // 2, kernel_size=(1, 1))
        self.agp = nn.AdaptiveAvgPool2d((1, 1))

    def forward(self, x: torch.Tensor):
        """ Forward function of PolarizedSelfAttention.

        :param x: the input feature map, shape=(bs, c, h, w)

        return:
            - out: the output feature map, shape=(bs, c, h, w)

        """

        # ---- Get the shape of x ---- #
        b, c, h, w = x.size()

        # ---- Channel-only Self-Attention ---- #
        channel_wv = self.ch_wv(x)  # c => c // 2 : (bs, c//2, h, w)
        channel_wq = self.ch_wq(x)  # c => 1 : (bs, 1, h, w)
        channel_wv = channel_wv.reshape(b, c // 2, -1)  # (bs, c//2, h * w) flatten feature map to pixel by pixel
        channel_wq = channel_wq.reshape(b, -1, 1)  # (bs, h * w, 1)
        channel_wq = self.softmax(channel_wq)  # get weight of each pixel of channels
        channel_wz = torch.matmul(channel_wv, channel_wq).unsqueeze(-1)  # (bs, c//2, 1, 1)
        channel_weight = (self.sigmoid(self.ln(self.ch_wz(channel_wz).reshape(b, c, 1).permute(0, 2, 1))).
                          permute(0, 2, 1).reshape(b, c, 1, 1))  # (bs, c, 1, 1)
        channel_out = channel_weight * x  # Channel-only attention

        # ---- Spatial-only Self-Attention ---- #
        spatial_wv = self.sp_wv(channel_out)  # (bs, c//2, h, w) why channel_out rather than x
        spatial_wq = self.sp_wq(channel_out)  # (bs, c//2, h, w)
        spatial_wq = self.agp(spatial_wq)  # (bs, c//2, 1, 1)
        spatial_wv = spatial_wv.reshape(b, c // 2, -1)  # (bs, c//2, h*w)
        spatial_wq = spatial_wq.permute(0, 2, 3, 1).reshape(b, 1, c // 2)  # (bs, 1, c//2)
        spatial_wz = torch.matmul(spatial_wq, spatial_wv)  # (bs, 1, h*w)
        spatial_weight = self.sigmoid(spatial_wz.reshape(b, 1, h, w))  # (bs, 1, h, w)
        spatial_out = spatial_weight * channel_out  # Spatial-only attention

        # ---- Add attention ---- #
        out = spatial_out + channel_out
        return out


class BasicConv2d(nn.Module):
    """ The Basic 2d Conv layer for easy using (set a lot of default hyperparams).
        Adjust the channels using 1x1 Conv, while extracting feature.

    You might have some questions for this 2d Conv layer:
        - Why the BasicConv2d is 1x1 Conv ?
            This is indeed a question worth exploring.
            For the time being, we have chosen 1x1 because it works better in our tests.

        - Why the BasicConv2d has no BN, Relu or Dropout ?
            This is indeed a question worth exploring.
            Now, we designed like this, just because it works better in our tests.

    """

    def __init__(self, in_channels: int, out_channels: int,
                 kernel_size: int = 1, stride: int = 1, padding: int = 0, dilation: int = 1, bias: bool = False):
        """ Init function of BasicConv2d, set a lot of default hyperparams,

        :param in_channels: input channels num
        :param out_channels: output channels num
        :param kernel_size: the kernel size
        :param stride: the conv stride
        :param padding: whether padding
        :param dilation: the dilation ratio
        :param bias: need bias or not

        """

        super(BasicConv2d, self).__init__()

        # ---- Define the nn.Conv2d layer ---- #
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding, dilation, groups=1, bias=bias)

    def forward(self, x: torch.Tensor):
        """ Forward function of BasicConv2d, just do the convolutional operation.

        :param x: the input feature map, shape=(bs, in_channels, h, w)

        return:
            - x: the output feature map, shape=(bs, out_channels, h, w)

        """

        x = self.conv(x)
        return x
