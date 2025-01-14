import torch
import torch.nn as nn
from models.BaseContextModel import ContextCTRModel
from utils.layers import MLP_Block

class FinalMLPBase:
    # Parse and define model arguments specific
    @staticmethod
    def parse_model_args_finalmlp(parser):
        parser.add_argument('--emb_size', type=int, default=64, help='Size of embedding vectors.')
        parser.add_argument('--mlp1_hidden_units', type=str, default='[64,64,64]', help='Hidden units list of MLP1.')
        parser.add_argument('--mlp1_hidden_activations', type=str, default='ReLU', help='Hidden activation function of MLP1.')
        parser.add_argument('--mlp1_dropout', type=float, default=0, help='Dropout rate of MLP1.')
        parser.add_argument('--mlp1_batch_norm', type=int, default=0, help='Whether to use batch normalization in MLP1.')
        parser.add_argument('--mlp2_hidden_units', type=str, default='[64,64,64]', help='Hidden units list of MLP2.')
        parser.add_argument('--mlp2_hidden_activations', type=str, default='ReLU', help='Hidden activation function of MLP2.')
        parser.add_argument('--mlp2_dropout', type=float, default=0, help='Dropout rate of MLP2.')
        parser.add_argument('--mlp2_batch_norm', type=int, default=0, help='Whether to use batch normalization in MLP2.')
        parser.add_argument('--use_fs', type=int, default=1, help='Whether to use feature selection module.')
        parser.add_argument('--fs_hidden_units', type=str, default='[64]', help='Hidden units list of feature selection module.')
        parser.add_argument('--fs1_context', type=str, default='', help='Names of context features used for MLP1, split by commas.')
        parser.add_argument('--fs2_context', type=str, default='', help='Names of context features used for MLP2, split by commas.')
        parser.add_argument('--num_heads', type=int, default=1, help='Number of heads in the fusion module.')
        return parser

    def _define_init(self, args, corpus):
        self.embedding_dim = args.emb_size
        self.use_fs = args.use_fs
        self.fs1_context = [f for f in args.fs1_context.split(",") if f]
        self.fs2_context = [f for f in args.fs2_context.split(",") if f]
        self._define_params_finalmlp(args)
        self.apply(self.init_weights)

    # embedding layers
    def _define_params_finalmlp(self, args):
        self.embedding_dict = nn.ModuleDict({
            f: nn.Embedding(self.feature_max[f], self.embedding_dim)
            if f.endswith('_c') or f.endswith('_id')
            else nn.Linear(1, self.embedding_dim, bias=False)
            for f in self.context_features
        })
        self.feature_dim = self.embedding_dim * len(self.embedding_dict)

        # Define MLPs
        self.mlp1 = MLP_Block(
            input_dim=self.feature_dim,
            output_dim=None,
            hidden_units=eval(args.mlp1_hidden_units),
            hidden_activations=args.mlp1_hidden_activations,
            dropout_rates=args.mlp1_dropout,
            batch_norm=args.mlp1_batch_norm
        )
        
        self.mlp2 = MLP_Block(
            input_dim=self.feature_dim,
            output_dim=None,
            hidden_units=eval(args.mlp2_hidden_units),
            hidden_activations=args.mlp2_hidden_activations,
            dropout_rates=args.mlp2_dropout,
            batch_norm=args.mlp2_batch_norm
        )

        if self.use_fs:
            self.fs_module = FeatureSelection(
                {},
                self.feature_dim,
                self.embedding_dim,
                eval(args.fs_hidden_units),
                self.fs1_context,
                self.fs2_context,
                self.feature_max
            )

        self.fusion_module = InteractionAggregation(
            eval(args.mlp1_hidden_units)[-1],
            eval(args.mlp2_hidden_units)[-1],
            output_dim=1,
            num_heads=args.num_heads
        )

    # Forward pass
    def forward(self, feed_dict):
        user_emb = self.embedding_dict['user_id'](feed_dict['user_id']).unsqueeze(1).unsqueeze(1)

        item_emb = torch.stack([
            self.embedding_dict[f](feed_dict[f])
            if f.endswith('_c') or f.endswith('_id')
            else self.embedding_dict[f](feed_dict[f].float().unsqueeze(-1))
            for f in self.context_features if f == 'item_id' or f.startswith('i_')
        ], dim=-2)

        situation_vectors = torch.stack([
            self.embedding_dict[f](feed_dict[f].long())
            if f.endswith('_c')
            else self.embedding_dict[f](feed_dict[f].float().unsqueeze(-1))
            for f in self.context_features if f.startswith('c_')
        ], dim=-2).unsqueeze(1)

        X = torch.cat([
            user_emb.repeat(1, item_emb.shape[1], 1, 1),
            item_emb,
            situation_vectors.repeat(1, item_emb.shape[1], 1, 1)
        ], dim=-2)

        flat_emb = X.flatten(start_dim=-2)

        if self.use_fs:
            feat1, feat2 = self.fs_module(feed_dict, flat_emb)
        else:
            feat1, feat2 = flat_emb, flat_emb

        mlp1_output = self.mlp1(feat1.view(-1, feat1.shape[-1])).view(feat1.shape[0], feat1.shape[1], -1)
        mlp2_output = self.mlp2(feat2.view(-1, feat2.shape[-1])).view(feat2.shape[0], feat2.shape[1], -1)

        y_pred = self.fusion_module(mlp1_output, mlp2_output)

        return {'prediction': y_pred}

# define the final model
class FinalMLPCTR(ContextCTRModel, FinalMLPBase):
    reader, runner = 'ContextReader', 'CTRRunner'
    extra_log_args = ['emb_size', 'loss_n', 'use_fs']

    @staticmethod
    def parse_model_args(parser):
        parser = FinalMLPBase.parse_model_args_finalmlp(parser)
        return ContextCTRModel.parse_model_args(parser)

    def __init__(self, args, corpus):
        super().__init__(args, corpus)
        self._define_init(args, corpus)

    def forward(self, feed_dict):
        out_dict = FinalMLPBase.forward(self, feed_dict)
        out_dict['prediction'] = out_dict['prediction'].view(-1).sigmoid()
        out_dict['label'] = feed_dict['label'].view(-1)
        return out_dict

class FeatureSelection(nn.Module):
    # Feature selection module
    def __init__(self, feature_map, feature_dim, embedding_dim, fs_hidden_units, fs1_context, fs2_context, feature_maxn):
        super().__init__()
        self.fs1_context, self.fs2_context = fs1_context, fs2_context
        self.fs1_ctx_emb = self._init_context_embeddings(fs1_context, embedding_dim, feature_maxn)
        self.fs2_ctx_emb = self._init_context_embeddings(fs2_context, embedding_dim, feature_maxn)

        self.fs1_gate = MLP_Block(
            input_dim=embedding_dim * max(1, len(fs1_context)),
            output_dim=feature_dim,
            hidden_units=fs_hidden_units,
            hidden_activations="ReLU",
            output_activation="Sigmoid",
            batch_norm=False
        )

        self.fs2_gate = MLP_Block(
            input_dim=embedding_dim * max(1, len(fs2_context)),
            output_dim=feature_dim,
            hidden_units=fs_hidden_units,
            hidden_activations="ReLU",
            output_activation="Sigmoid",
            batch_norm=False
        )

    def _init_context_embeddings(self, context_list, embedding_dim, feature_maxn):
        embeddings = []
        for ctx in context_list:
            if ctx.endswith("_c") and ctx in feature_maxn:
                embeddings.append(nn.Embedding(feature_maxn[ctx], embedding_dim))
            elif ctx.endswith("_f"):
                embeddings.append(nn.Linear(1, embedding_dim))
            else:
                raise ValueError(f"Undefined context {ctx}")
        return nn.ModuleList(embeddings) if embeddings else None

    def forward(self, feed_dict, flat_emb):
        fs1_input = self._prepare_context_input(feed_dict, flat_emb, self.fs1_ctx_emb, self.fs1_context)
        fs2_input = self._prepare_context_input(feed_dict, flat_emb, self.fs2_ctx_emb, self.fs2_context)

        gt1 = self.fs1_gate(fs1_input) * 2
        gt2 = self.fs2_gate(fs2_input) * 2

        return flat_emb * gt1, flat_emb * gt2

    def _prepare_context_input(self, feed_dict, flat_emb, context_emb, context_features):
        if not context_features:
            return torch.zeros(flat_emb.size(0), flat_emb.size(1), flat_emb.size(-1))
        inputs = []
        for i, ctx in enumerate(context_features):
            ctx_emb = context_emb[i](feed_dict[ctx].float().unsqueeze(-1)) if ctx.endswith("_f") else context_emb[i](feed_dict[ctx].long())
            inputs.append(ctx_emb.unsqueeze(1).repeat(1, flat_emb.size(1), 1) if ctx_emb.ndim == 2 else ctx_emb)
        return torch.cat(inputs, dim=-1)


class InteractionAggregation(nn.Module):
    def __init__(self, x_dim, y_dim, output_dim=1, num_heads=1):
        super().__init__()
        assert x_dim % num_heads == 0 and y_dim % num_heads == 0, "Input dimensions must be divisible by num_heads!"

        self.num_heads = num_heads
        self.output_dim = output_dim
        self.head_x_dim = x_dim // num_heads
        self.head_y_dim = y_dim // num_heads

        self.w_x = nn.Linear(x_dim, output_dim)
        self.w_y = nn.Linear(y_dim, output_dim)
        self.w_xy = nn.Parameter(torch.empty(num_heads * self.head_x_dim * self.head_y_dim, output_dim))
        nn.init.xavier_normal_(self.w_xy)

    def forward(self, x, y):
        batch_size, item_num = x.shape[0], x.shape[1]
        output = self.w_x(x) + self.w_y(y)

        head_x = x.view(batch_size, item_num, self.num_heads, self.head_x_dim).flatten(0, 1)
        head_y = y.view(batch_size, item_num, self.num_heads, self.head_y_dim).flatten(0, 1)

        xy = torch.matmul(
            torch.matmul(head_x.unsqueeze(2), self.w_xy.view(self.num_heads, self.head_x_dim, -1))
            .view(-1, self.num_heads, self.output_dim, self.head_y_dim),
            head_y.unsqueeze(-1)
        ).squeeze(-1)

        output += xy.sum(dim=1).view(batch_size, item_num, -1)

        return output.squeeze(-1)
