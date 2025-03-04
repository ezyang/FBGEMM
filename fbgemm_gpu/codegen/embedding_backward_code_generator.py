#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# All rights reserved.
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import collections
import os

import jinja2

parser = argparse.ArgumentParser()
# By default the source template files are in the same folder as
# embedding_backward_code_generator.py;
# The install dir is by default the same as the current folder.
parser.add_argument("--install_dir", default=".", help="where to put generated file")
args, _ = parser.parse_known_args()


env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(os.path.abspath(__file__)))
)
env.globals["max_embedding_dim"] = 1024
env.globals["dense"] = False


def write(filename, s):
    with open(os.path.join(args.install_dir, filename), "w") as f:
        f.write(s)


def _arg_constructor(type: str, name: str, gpu: bool = True, precision: int = 32):
    return (
        f"{name}.packed_accessor{precision}<{type}, 1, RestrictPtrTraits>()"
        if gpu
        else f"auto {name}_accessor = {name}.accessor<{type}, 1>()"
    )


def _arg(type: str, name: str, precision: int = 32):
    return f"PackedTensorAccessor{precision}<{type}, 1, RestrictPtrTraits> {name}"


def acc_cache_tensor_arg_constructor(name):
    return _arg_constructor("acc_type<cache_t, true>", name, precision=64)


def acc_cache_tensor_arg(name):
    return _arg("acc_type<cache_t, true>", name, precision=64)


def long_tensor_arg_constructor(name, gpu=True):
    return _arg_constructor("int64_t", name, gpu=gpu)


def long_tensor_arg(name):
    return _arg("int64_t", name)


def int_tensor_arg_constructor(name, gpu=True):
    return _arg_constructor("int32_t", name, gpu=gpu)


def int_tensor_arg(name):
    return _arg("int32_t", name)


def host_accessor_constructor(name):
    return _arg_constructor("acc_type<scalar_t, true>", name, gpu=False)


def tensor_arg(name):
    return f"Tensor {name}"


def double_arg(name):
    return f"double {name}"


def float_arg(name):
    return f"float {name}"


def int64_arg(name):
    return f"int64_t {name}"


def int_arg(name):
    return f"int {name}"


def generate(**kwargs):
    gen_args = kwargs["args"]

    # Generates CUDA variants.
    kwargs["args"] = gen_args["cuda"]

    template = env.get_template("embedding_backward_split_template.cu")
    src_cu = template.render(weighted=False, **kwargs)
    write(
        f"gen_embedding_backward_{kwargs.get('optimizer')}_split_unweighted_cuda.cu",
        src_cu,
    )
    src_cu = template.render(weighted=True, **kwargs)
    write(
        f"gen_embedding_backward_{kwargs.get('optimizer')}_split_weighted_cuda.cu",
        src_cu,
    )
    if not kwargs.get("dense"):
        template = env.get_template("embedding_backward_split_host_template.cpp")
        src_cpp = template.render(**kwargs)
        write(f"gen_embedding_backward_split_{kwargs.get('optimizer')}.cpp", src_cpp)

        # Generates Python invoker for CUDA + CPU
        template = env.get_template("split_embedding_codegen_lookup_invoker.template")
        src_py = template.render(is_fbcode=args.is_fbcode, **kwargs)
        write(f"lookup_{kwargs.get('optimizer')}.py", src_py)

    # Generates CPU variants.
    kwargs["args"] = gen_args["cpu"]

    is_approx = "approx" in kwargs.get("optimizer")
    template = (
        env.get_template("embedding_backward_split_cpu_approx_template.cpp")
        if is_approx
        else env.get_template("embedding_backward_split_cpu_template.cpp")
    )

    src_cpp = template.render(**kwargs)
    write(
        f"gen_embedding_backward_{kwargs.get('optimizer')}_split_cpu.cpp",
        src_cpp,
    )

    if not kwargs.get("dense"):
        template = env.get_template("embedding_backward_split_host_cpu_template.cpp")
        src_cpp = template.render(**kwargs)
        write(
            f"gen_embedding_backward_split_{kwargs.get('optimizer')}_cpu.cpp", src_cpp
        )


Args = collections.namedtuple(
    "Args",
    [
        "split_kernel_args",
        "split_kernel_arg_constructors",
        "split_host_accessor_constructors",
        "split_function_args",
        "split_saved_tensors",
        "split_tensors",
        "saved_data",
        "split_function_arg_names",
        "split_function_schemas",
        "split_variables",
    ],
)

TENSOR, INT_TENSOR, LONG_TENSOR, INT, FLOAT = range(5)


def make_args(arg_spec):
    def make_kernel_arg(ty, name):
        return {
            TENSOR: acc_cache_tensor_arg,
            INT_TENSOR: int_tensor_arg,
            LONG_TENSOR: long_tensor_arg,
            INT: int64_arg,
            FLOAT: float_arg,
        }[ty](name)

    def make_kernel_arg_constructor(ty, name):
        return {
            TENSOR: acc_cache_tensor_arg_constructor,
            INT_TENSOR: int_tensor_arg_constructor,
            LONG_TENSOR: long_tensor_arg_constructor,
            INT: lambda x: x,
            FLOAT: lambda x: x,
        }[ty](name)

    def make_host_accessor_constructor(ty, name):
        return {
            TENSOR: host_accessor_constructor,
            INT_TENSOR: lambda x: int_tensor_arg_constructor(x, gpu=False),
            LONG_TENSOR: lambda x: long_tensor_arg_constructor(x, gpu=False),
            INT: lambda x: "",
            FLOAT: lambda x: "",
        }[ty](name)

    def make_function_arg(ty, name):
        return {
            TENSOR: tensor_arg,
            INT_TENSOR: tensor_arg,
            LONG_TENSOR: tensor_arg,
            INT: int64_arg,
            FLOAT: double_arg,
        }[ty](name)

    def make_function_schema_arg(ty, name):
        return {
            TENSOR: tensor_arg,
            INT_TENSOR: tensor_arg,
            LONG_TENSOR: tensor_arg,
            INT: int_arg,
            FLOAT: float_arg,
        }[ty](name)

    def make_ivalue_cast(ty):
        return {INT: "toInt", FLOAT: "toDouble"}[ty]

    def make_args_for_compute_device(split_arg_spec):
        return Args(
            split_kernel_args=[
                make_kernel_arg(ty, name) for (ty, name) in split_arg_spec
            ],
            split_kernel_arg_constructors=[
                make_kernel_arg_constructor(ty, name) for (ty, name) in split_arg_spec
            ],
            split_host_accessor_constructors=[
                make_host_accessor_constructor(ty, name)
                for (ty, name) in split_arg_spec
            ],
            split_function_args=[
                make_function_arg(ty, name) for (ty, name) in split_arg_spec
            ],
            split_tensors=[name for (ty, name) in arg_spec if ty == TENSOR],
            split_saved_tensors=[
                name
                for (ty, name) in split_arg_spec
                if ty in (TENSOR, INT_TENSOR, LONG_TENSOR)
            ],
            saved_data=[
                (name, make_ivalue_cast(ty)) for (ty, name) in arg_spec if ty != TENSOR
            ],
            split_function_arg_names=[name for (ty, name) in split_arg_spec],
            split_function_schemas=[
                make_function_schema_arg(ty, name) for (ty, name) in split_arg_spec
            ],
            split_variables=["Variable()" for _ in split_arg_spec],
        )

    split_arg_spec = []
    for (ty, arg) in arg_spec:
        if ty in (FLOAT, INT):
            split_arg_spec.append((ty, arg))
        else:
            assert ty == TENSOR
            split_arg_spec.extend(
                [
                    (TENSOR, f"{arg}_host"),
                    (INT_TENSOR, f"{arg}_placements"),
                    (LONG_TENSOR, f"{arg}_offsets"),
                ]
            )
    cpu = make_args_for_compute_device(split_arg_spec)

    split_arg_spec = []
    for (ty, arg) in arg_spec:
        if ty in (FLOAT, INT):
            split_arg_spec.append((ty, arg))
        else:
            assert ty == TENSOR
            split_arg_spec.extend(
                [
                    (TENSOR, f"{arg}_dev"),
                    (TENSOR, f"{arg}_uvm"),
                    (INT_TENSOR, f"{arg}_placements"),
                    (LONG_TENSOR, f"{arg}_offsets"),
                ]
            )
    cuda = make_args_for_compute_device(split_arg_spec)

    return {"cpu": cpu, "cuda": cuda}


def adagrad():
    split_weight_update = """
      Vec4T<cache_t> m_t(&momentum1[idx * D + d]);
      m_t.acc.x += grad.acc.x * grad.acc.x;
      m_t.acc.y += grad.acc.y * grad.acc.y;
      m_t.acc.z += grad.acc.z * grad.acc.z;
      m_t.acc.w += grad.acc.w * grad.acc.w;
      m_t.store(&momentum1[idx * D + d]);

      weight_new.acc.x -= learning_rate * grad.acc.x / (sqrtf(m_t.acc.x) + eps);
      weight_new.acc.y -= learning_rate * grad.acc.y / (sqrtf(m_t.acc.y) + eps);
      weight_new.acc.z -= learning_rate * grad.acc.z / (sqrtf(m_t.acc.z) + eps);
      weight_new.acc.w -= learning_rate * grad.acc.w / (sqrtf(m_t.acc.w) + eps);
    """
    split_weight_update_cpu = """
      for (int64_t d = 0; d < D; ++d) {
        momentum1_host_accessor[embedding_begin + d] +=
            grad_buffer[d] * grad_buffer[d];
        host_weights_data[embedding_begin + d] -=
            learning_rate * grad_buffer[d] /
            (sqrt(momentum1_host_accessor[embedding_begin + d]) + eps);
      }
    """

    generate(
        optimizer="adagrad",
        args=make_args(
            [(TENSOR, "momentum1"), (FLOAT, "eps"), (FLOAT, "learning_rate")]
        ),
        split_precomputation="",
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def table_info_precomputation(momentum_prefix="momentum1"):
    template = """
      // table_begin -> (E, D, {momentum_prefix}_row_begin).
      std::map<int64_t, std::tuple<int64_t, int64_t, int64_t>> table_info_map;
      for (int64_t t = 0; t < T; ++t) {
        const auto D = D_offsets_data[t + 1] - D_offsets_data[t];
        const auto table_begin = weights_offsets_data[t];
        const auto {momentum_prefix}_row_begin = {momentum_prefix}_offsets_data[t];
        table_info_map[table_begin] = std::make_tuple(0, D, {momentum_prefix}_row_begin);
      }
      int64_t previous_table_begin = host_weights.numel();
      // NOTE: table_info_map is sorted by table_begin!
      for (auto it = table_info_map.rbegin(); it != table_info_map.rend(); ++it) {
        const auto D = std::get<1>(it->second);
        // Calculates number of rows of each table.
        std::get<0>(it->second) = (previous_table_begin - it->first) / D;
        previous_table_begin = it->first;
      }
    """
    return template.replace("{momentum_prefix}", momentum_prefix)


def rowwise_adagrad():
    split_weight_update = """
      weight_new.fma_(grad, -multiplier);
    """
    split_precomputation = """
    acc_type<cache_t, true> g_local_sum_square = 0.0;
    #pragma unroll kMaxVecsPerThread
    for (int32_t i = 0;
        i < kMaxVecsPerThread && 4 * kWarpSize * i + threadIdx.x * 4 < D;
        ++i) {
    g_local_sum_square += grad_sum[i].acc.x * grad_sum[i].acc.x +
        grad_sum[i].acc.y * grad_sum[i].acc.y +
        grad_sum[i].acc.z * grad_sum[i].acc.z +
        grad_sum[i].acc.w * grad_sum[i].acc.w;
    }
    const acc_type<cache_t, true> g_avg_square =
        warpReduceAllSum<acc_type<cache_t, true>>(g_local_sum_square) / D;

    acc_type<cache_t, true> multiplier;
    if (threadIdx.x == 0) {
        acc_type<cache_t, true> new_sum_square_grads = momentum1[idx] + g_avg_square;
        momentum1[idx] = new_sum_square_grads;
        multiplier = learning_rate / (sqrtf(new_sum_square_grads) + eps);
    }
    multiplier = __shfl_sync(0xFFFFFFFF, multiplier, 0);
    """
    split_weight_update_cpu = """
        grad_t g_local_sum_square = 0.0;
        for (int64_t d = 0; d < D; ++d) {
            g_local_sum_square += grad_buffer[d] * grad_buffer[d];
        }
        auto g_avg_square = g_local_sum_square / D;
        grad_t new_sum_square_grads = momentum1_host_accessor[momentum1_offsets_data[feature_begin] + batched_csc.column_indices[c]] + g_avg_square;
        momentum1_host_accessor[momentum1_offsets_data[feature_begin] + batched_csc.column_indices[c]] = new_sum_square_grads;
        grad_t multiplier;
        multiplier = learning_rate / (sqrtf(new_sum_square_grads) + eps);
        for (int64_t d = 0; d < D; ++d) {
            host_weights_data[embedding_begin + d] -= grad_buffer[d] * multiplier;
        }
    """

    generate(
        optimizer="rowwise_adagrad",
        args=make_args(
            [(TENSOR, "momentum1"), (FLOAT, "eps"), (FLOAT, "learning_rate")]
        ),
        split_precomputation=split_precomputation,
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def approx_rowwise_adagrad():
    split_weight_update = """
      weight_new.fma_(grad, -multiplier);
      assert(false); // approx rowwise AdaGrad is not supported on GPU
    """
    split_precomputation = """
    acc_type<cache_t, true> g_local_sum_square = 0.0;
    #pragma unroll kMaxVecsPerThread
    for (int32_t i = 0;
        i < kMaxVecsPerThread && 4 * kWarpSize * i + threadIdx.x * 4 < D;
        ++i) {
    g_local_sum_square += grad_sum[i].acc.x * grad_sum[i].acc.x +
        grad_sum[i].acc.y * grad_sum[i].acc.y +
        grad_sum[i].acc.z * grad_sum[i].acc.z +
        grad_sum[i].acc.w * grad_sum[i].acc.w;
    }
    const acc_type<cache_t, true> g_avg_square =
        warpReduceAllSum<acc_type<cache_t, true>>(g_local_sum_square) / D;

    acc_type<cache_t, true> multiplier;
    if (threadIdx.x == 0) {
        acc_type<cache_t, true> new_sum_square_grads = momentum1[idx] + g_avg_square;
        momentum1[idx] = new_sum_square_grads;
        multiplier = learning_rate / (sqrtf(new_sum_square_grads) + eps);
    }
    multiplier = __shfl_sync(0xFFFFFFFF, multiplier, 0);
    """
    split_weight_update_cpu = ""  # will use FBGEMM CPU JIT'ed kernel

    generate(
        optimizer="approx_rowwise_adagrad",
        args=make_args(
            [(TENSOR, "momentum1"), (FLOAT, "eps"), (FLOAT, "learning_rate")]
        ),
        split_precomputation=split_precomputation,
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def sgd():
    split_weight_update = """
      weight_new.fma_(grad, -learning_rate);
    """
    split_weight_update_cpu = """
      for (int64_t d = 0; d < D; ++d) {
        host_weights_data[embedding_begin + d] -= learning_rate * grad_buffer[d];
      }
    """

    generate(
        optimizer="sgd",
        args=make_args([(FLOAT, "learning_rate")]),
        split_precomputation="",
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def approx_sgd():
    split_weight_update = """
      // approx_sgd not supported for GPU.
      // Just do the same thing as exact sgd to avoid unused variable warning.
      weight_new.fma_(grad, -learning_rate);
      assert(false); // approx SGD is not supported on GPU
    """
    split_weight_update_cpu = """
      host_weights_data[embedding_begin + d] -= learning_rate * grad_val;
    """

    generate(
        optimizer="approx_sgd",
        args=make_args([(FLOAT, "learning_rate")]),
        split_precomputation="",
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def lamb():
    split_precomputation = """
  acc_type<cache_t, true> weight_sum_sq = 0.0;
  acc_type<cache_t, true> rtw_sum_sq = 0.0;
  auto weight_row = WeightRow<emb_t, cache_t, acc_type<cache_t, true>>(weights, cache_weights, D, nullptr);
  float2 qparams;
  if (std::is_same<emb_t, uint8_t>::value && !cache_weights) {
    qparams = weight_row.load_qparams();
  }
#pragma unroll 1
  for (int32_t i = 0;
      i < kMaxVecsPerThread && 4 * kWarpSize * i + threadIdx.x * 4 < D;
      ++i) {
    int32_t d = 4 * kWarpSize * i + threadIdx.x * 4;
    Vec4T<acc_type<cache_t, true>> weight = weight_row.load(d, qparams);
    Vec4T<acc_type<cache_t, true>> m1(&momentum1[idx * D + d]);

    m1.acc.x = beta1 * m1.acc.x + (1.0 - beta1) * grad_sum[i].acc.x;
    m1.acc.y = beta1 * m1.acc.y + (1.0 - beta1) * grad_sum[i].acc.y;
    m1.acc.z = beta1 * m1.acc.z + (1.0 - beta1) * grad_sum[i].acc.z;
    m1.acc.w = beta1 * m1.acc.w + (1.0 - beta1) * grad_sum[i].acc.w;
    m1.store(&momentum1[idx * D + d]);

    Vec4T<acc_type<cache_t, true>> m2(&momentum2[idx * D + d]);
    m2.acc.x = beta2 * m2.acc.x + (1.0 - beta2) * grad_sum[i].acc.x * grad_sum[i].acc.x;
    m2.acc.y = beta2 * m2.acc.y + (1.0 - beta2) * grad_sum[i].acc.y * grad_sum[i].acc.y;
    m2.acc.z = beta2 * m2.acc.z + (1.0 - beta2) * grad_sum[i].acc.z * grad_sum[i].acc.z;
    m2.acc.w = beta2 * m2.acc.w + (1.0 - beta2) * grad_sum[i].acc.w * grad_sum[i].acc.w;
    m2.store(&momentum2[idx * D + d]);

    // now, we are finished with grad_sum. We can *reuse* grad_sum to store r_t + weight_decay * weight;
    grad_sum[i].acc.x = (m1.acc.x / (1.0 - powf(beta1, iter))) / (sqrtf((m2.acc.x / (1.0 - powf(beta2, iter)))) + eps) + weight_decay * weight.acc.x;
    grad_sum[i].acc.y = (m1.acc.y / (1.0 - powf(beta1, iter))) / (sqrtf((m2.acc.y / (1.0 - powf(beta2, iter)))) + eps) + weight_decay * weight.acc.y;
    grad_sum[i].acc.z = (m1.acc.z / (1.0 - powf(beta1, iter))) / (sqrtf((m2.acc.z / (1.0 - powf(beta2, iter)))) + eps) + weight_decay * weight.acc.z;
    grad_sum[i].acc.w = (m1.acc.w / (1.0 - powf(beta1, iter))) / (sqrtf((m2.acc.w / (1.0 - powf(beta2, iter)))) + eps) + weight_decay * weight.acc.w;

    weight_sum_sq += weight.acc.x * weight.acc.x + weight.acc.y * weight.acc.y + weight.acc.z * weight.acc.z + weight.acc.w * weight.acc.w;
    rtw_sum_sq += grad_sum[i].acc.x * grad_sum[i].acc.x + grad_sum[i].acc.y * grad_sum[i].acc.y + grad_sum[i].acc.z * grad_sum[i].acc.z + grad_sum[i].acc.w * grad_sum[i].acc.w;
  }
  const auto weight_norm =
      sqrtf(warpReduceAllSum<acc_type<cache_t, true>>(weight_sum_sq));
  const auto rtw_norm =
      sqrtf(warpReduceAllSum<acc_type<cache_t, true>>(rtw_sum_sq));
   const auto true_ratio = weight_norm / rtw_norm;
"""
    split_weight_update = """
      weight_new.fma_(grad, -learning_rate * true_ratio);
    """
    split_weight_update_cpu = ""

    generate(
        optimizer="lamb",
        args=make_args(
            [
                (TENSOR, "momentum1"),
                (TENSOR, "momentum2"),
                (FLOAT, "learning_rate"),
                (FLOAT, "eps"),
                (FLOAT, "beta1"),
                (FLOAT, "beta2"),
                (FLOAT, "weight_decay"),
                (INT, "iter"),
            ]
        ),
        split_precomputation=split_precomputation,
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def partial_rowwise_lamb():
    split_precomputation = """
    acc_type<cache_t, true> g_local_sum_square = 0.0;

    #pragma unroll kMaxVecsPerThread
    for (int32_t i = 0;
        i < kMaxVecsPerThread && 4 * kWarpSize * i + threadIdx.x * 4 < D;
        ++i) {
    g_local_sum_square += grad_sum[i].acc.x * grad_sum[i].acc.x +
        grad_sum[i].acc.y * grad_sum[i].acc.y +
        grad_sum[i].acc.z * grad_sum[i].acc.z +
        grad_sum[i].acc.w * grad_sum[i].acc.w;
    }
    const acc_type<cache_t, true> g_avg_square =
        warpReduceAllSum<acc_type<cache_t, true>>(g_local_sum_square) / D;

    acc_type<cache_t, true> m2;
    if (threadIdx.x == 0) {
        m2 = beta2 * momentum2[idx] + (1.0 - beta2) * g_avg_square;
        momentum2[idx] = m2;
    }
    m2 = __shfl_sync(0xFFFFFFFF, m2, 0);
    acc_type<cache_t, true> m2_hat = 1.0 / (sqrtf((m2 / (1.0 - powf(beta2, iter)))) + eps);

    acc_type<cache_t, true> weight_sum_sq = 0.0;
    acc_type<cache_t, true> rtw_sum_sq = 0.0;
    auto weight_row = WeightRow<emb_t, cache_t, acc_type<cache_t, true>>(weights, cache_weights, D, nullptr);
    float2 qparams;
    if (std::is_same<emb_t, uint8_t>::value && !cache_weights) {
        qparams = weight_row.load_qparams();
    }
    #pragma unroll kMaxVecsPerThread
    for (int32_t i = 0;
        i < kMaxVecsPerThread && 4 * kWarpSize * i + threadIdx.x * 4 < D;
        ++i) {
        int32_t d = 4 * kWarpSize * i + threadIdx.x * 4;

        Vec4T<acc_type<cache_t, true>> m1(&momentum1[idx * D + d]);
        m1.acc.x = beta1 * m1.acc.x + (1.0 - beta1) * grad_sum[i].acc.x;
        m1.acc.y = beta1 * m1.acc.y + (1.0 - beta1) * grad_sum[i].acc.y;
        m1.acc.z = beta1 * m1.acc.z + (1.0 - beta1) * grad_sum[i].acc.z;
        m1.acc.w = beta1 * m1.acc.w + (1.0 - beta1) * grad_sum[i].acc.w;
        m1.store(&momentum1[idx * D + d]);

        // now, we are finished with grad_sum. We can *reuse* grad_sum to store r_t + weight_decay * weight;
        Vec4T<acc_type<cache_t, true>> weight = weight_row.load(d, qparams);
        grad_sum[i].acc.x = (m1.acc.x / (1.0 - powf(beta1, iter))) * m2_hat + weight_decay * weight.acc.x;
        grad_sum[i].acc.y = (m1.acc.y / (1.0 - powf(beta1, iter))) * m2_hat + weight_decay * weight.acc.y;
        grad_sum[i].acc.z = (m1.acc.z / (1.0 - powf(beta1, iter))) * m2_hat + weight_decay * weight.acc.z;
        grad_sum[i].acc.w = (m1.acc.w / (1.0 - powf(beta1, iter))) * m2_hat + weight_decay * weight.acc.w;

        weight_sum_sq += weight.acc.x * weight.acc.x + weight.acc.y * weight.acc.y + weight.acc.z * weight.acc.z + weight.acc.w * weight.acc.w;
        rtw_sum_sq += grad_sum[i].acc.x * grad_sum[i].acc.x + grad_sum[i].acc.y * grad_sum[i].acc.y + grad_sum[i].acc.z * grad_sum[i].acc.z + grad_sum[i].acc.w * grad_sum[i].acc.w;
    }
    const auto weight_norm =
        sqrtf(warpReduceAllSum<acc_type<cache_t, true>>(weight_sum_sq));
    const auto rtw_norm =
        sqrtf(warpReduceAllSum<acc_type<cache_t, true>>(rtw_sum_sq));
    const auto true_ratio = weight_norm / rtw_norm;
    """

    split_weight_update = """
      weight_new.fma_(grad, -learning_rate * true_ratio);
    """
    split_weight_update_cpu = ""  # TODO

    generate(
        optimizer="partial_rowwise_lamb",
        args=make_args(
            [
                (TENSOR, "momentum1"),
                (TENSOR, "momentum2"),
                (FLOAT, "learning_rate"),
                (FLOAT, "eps"),
                (FLOAT, "beta1"),
                (FLOAT, "beta2"),
                (FLOAT, "weight_decay"),
                (INT, "iter"),
            ]
        ),
        split_precomputation=split_precomputation,
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def adam():
    split_weight_update = """
      Vec4T<cache_t> m_t(&momentum1[idx * D + d]);
      m_t.acc.x *= beta1;
      m_t.acc.y *= beta1;
      m_t.acc.z *= beta1;
      m_t.acc.w *= beta1;
      m_t.fma_(grad, 1.0 - beta1);
      m_t.store(&momentum1[idx * D + d]);

      Vec4T<cache_t> v_t(&momentum2[idx * D + d]);
      v_t.acc.x *= beta2;
      v_t.acc.y *= beta2;
      v_t.acc.z *= beta2;
      v_t.acc.w *= beta2;

      grad.acc.x *= grad.acc.x;
      grad.acc.y *= grad.acc.y;
      grad.acc.z *= grad.acc.z;
      grad.acc.w *= grad.acc.w;
      v_t.fma_(grad, 1.0 - beta2);
      v_t.store(&momentum2[idx * D + d]);

      weight_new.acc.x -= learning_rate * (m_t.acc.x / (1.0 - powf(beta1, iter)) / (sqrtf((v_t.acc.x / (1.0 - powf(beta2, iter)))) + eps) + weight_decay * weight_new.acc.x);
      weight_new.acc.y -= learning_rate * (m_t.acc.y / (1.0 - powf(beta1, iter)) / (sqrtf((v_t.acc.y / (1.0 - powf(beta2, iter)))) + eps) + weight_decay * weight_new.acc.y);
      weight_new.acc.z -= learning_rate * (m_t.acc.z / (1.0 - powf(beta1, iter)) / (sqrtf((v_t.acc.z / (1.0 - powf(beta2, iter)))) + eps) + weight_decay * weight_new.acc.z);
      weight_new.acc.w -= learning_rate * (m_t.acc.w / (1.0 - powf(beta1, iter)) / (sqrtf((v_t.acc.w / (1.0 - powf(beta2, iter)))) + eps) + weight_decay * weight_new.acc.w);
    """
    split_weight_update_cpu = ""  # TODO

    generate(
        optimizer="adam",
        args=make_args(
            [
                (TENSOR, "momentum1"),
                (TENSOR, "momentum2"),
                (FLOAT, "learning_rate"),
                (FLOAT, "eps"),
                (FLOAT, "beta1"),
                (FLOAT, "beta2"),
                (FLOAT, "weight_decay"),
                (INT, "iter"),
            ]
        ),
        split_precomputation="",
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def partial_rowwise_adam():
    split_precomputation = """
    acc_type<cache_t, true> g_local_sum_square = 0.0;
    #pragma unroll kMaxVecsPerThread
    for (int32_t i = 0;
        i < kMaxVecsPerThread && 4 * kWarpSize * i + threadIdx.x * 4 < D;
        ++i) {
    g_local_sum_square += grad_sum[i].acc.x * grad_sum[i].acc.x +
        grad_sum[i].acc.y * grad_sum[i].acc.y +
        grad_sum[i].acc.z * grad_sum[i].acc.z +
        grad_sum[i].acc.w * grad_sum[i].acc.w;
    }
    const acc_type<cache_t, true> g_avg_square =
        warpReduceAllSum<acc_type<cache_t, true>>(g_local_sum_square) / D;

    acc_type<cache_t, true> v_hat_t;
    if (threadIdx.x == 0) {
        acc_type<cache_t, true> v_t = momentum2[idx] * beta2 + g_avg_square * (1.0 - beta2);
        momentum2[idx] = v_t;
        v_hat_t = v_t / (1.0 - powf(beta2, iter));
    }
    v_hat_t = __shfl_sync(0xFFFFFFFF, v_hat_t, 0);
    """

    split_weight_update = """
      Vec4T<cache_t> m_t(&momentum1[idx * D + d]);
      m_t.acc.x *= beta1;
      m_t.acc.y *= beta1;
      m_t.acc.z *= beta1;
      m_t.acc.w *= beta1;
      m_t.fma_(grad, 1.0 - beta1);
      m_t.store(&momentum1[idx * D + d]);

      weight_new.acc.x -= learning_rate * (m_t.acc.x / (1.0 - powf(beta1, iter)) / (sqrtf(v_hat_t) + eps) + weight_decay * weight_new.acc.x);
      weight_new.acc.y -= learning_rate * (m_t.acc.y / (1.0 - powf(beta1, iter)) / (sqrtf(v_hat_t) + eps) + weight_decay * weight_new.acc.y);
      weight_new.acc.z -= learning_rate * (m_t.acc.z / (1.0 - powf(beta1, iter)) / (sqrtf(v_hat_t) + eps) + weight_decay * weight_new.acc.z);
      weight_new.acc.w -= learning_rate * (m_t.acc.w / (1.0 - powf(beta1, iter)) / (sqrtf(v_hat_t) + eps) + weight_decay * weight_new.acc.w);
    """
    split_weight_update_cpu = ""  # TODO

    generate(
        optimizer="partial_rowwise_adam",
        args=make_args(
            [
                (TENSOR, "momentum1"),
                (TENSOR, "momentum2"),
                (FLOAT, "learning_rate"),
                (FLOAT, "eps"),
                (FLOAT, "beta1"),
                (FLOAT, "beta2"),
                (FLOAT, "weight_decay"),
                (INT, "iter"),
            ]
        ),
        split_precomputation=split_precomputation,
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def lars_sgd():
    split_precomputation = """
  acc_type<cache_t, true> weight_sum_sq = 0.0;
  acc_type<cache_t, true> grad_sum_sq = 0.0;

  auto weight_row = WeightRow<emb_t, cache_t, acc_type<cache_t, true>>(weights, cache_weights, D, nullptr);
  float2 qparams;
  if (std::is_same<emb_t, uint8_t>::value && !cache_weights) {
      qparams = weight_row.load_qparams();
  }
#pragma unroll kMaxVecsPerThread
  for (int32_t i = 0;
      i < kMaxVecsPerThread && 4 * kWarpSize * i + threadIdx.x * 4 < D;
      ++i) {
    int32_t d = 4 * kWarpSize * i + threadIdx.x * 4;
    Vec4T<acc_type<cache_t,true>> weight = weight_row.load(d, qparams);
    weight_sum_sq += weight.acc.x * weight.acc.x + weight.acc.y * weight.acc.y + weight.acc.z * weight.acc.z + weight.acc.w * weight.acc.w;
    grad_sum_sq += grad_sum[i].acc.x * grad_sum[i].acc.x + grad_sum[i].acc.y * grad_sum[i].acc.y + grad_sum[i].acc.z * grad_sum[i].acc.z + grad_sum[i].acc.w * grad_sum[i].acc.w;
  }
  const auto weight_norm =
      sqrtf(warpReduceAllSum<acc_type<cache_t, true>>(weight_sum_sq));
  const auto grad_norm =
      sqrtf(warpReduceAllSum<acc_type<cache_t, true>>(grad_sum_sq));
   const acc_type<cache_t, true> adjusted_lr = learning_rate * eta * weight_norm / (grad_norm + weight_decay * weight_norm);
"""

    split_weight_update = """
      Vec4T<cache_t> m1(&momentum1[idx * D + d]);
      m1.acc.x = momentum * m1.acc.x + adjusted_lr * (grad.acc.x + weight_decay * weight_new.acc.x);
      m1.acc.y = momentum * m1.acc.y + adjusted_lr * (grad.acc.y + weight_decay * weight_new.acc.y);
      m1.acc.z = momentum * m1.acc.z + adjusted_lr * (grad.acc.z + weight_decay * weight_new.acc.z);
      m1.acc.w = momentum * m1.acc.w + adjusted_lr * (grad.acc.w + weight_decay * weight_new.acc.w);
      m1.store(&momentum1[idx * D + d]);

      weight_new.acc.x -= m1.acc.x;
      weight_new.acc.y -= m1.acc.y;
      weight_new.acc.z -= m1.acc.z;
      weight_new.acc.w -= m1.acc.w;
    """
    split_weight_update_cpu = ""  # TODO

    generate(
        optimizer="lars_sgd",
        args=make_args(
            [
                (TENSOR, "momentum1"),
                (FLOAT, "learning_rate"),
                (FLOAT, "eta"),
                (FLOAT, "momentum"),
                (FLOAT, "weight_decay"),
            ]
        ),
        split_precomputation=split_precomputation,
        split_weight_update=split_weight_update,
        split_weight_update_cpu=split_weight_update_cpu,
    )


def forward_split():
    template = env.get_template("embedding_forward_split_template.cu")

    src_cu = template.render(weighted=False)
    write("gen_embedding_forward_split_unweighted_codegen_cuda.cu", src_cu)
    src_cu = template.render(weighted=True)
    write("gen_embedding_forward_split_weighted_codegen_cuda.cu", src_cu)

    src_cu = template.render(weighted=False, dense=True)
    write("gen_embedding_forward_dense_unweighted_codegen_cuda.cu", src_cu)
    src_cu = template.render(weighted=True, dense=True)
    write("gen_embedding_forward_dense_weighted_codegen_cuda.cu", src_cu)


def backward_indices():
    template = env.get_template("embedding_backward_split_indice_weights_template.cu")
    src_cu = template.render()
    write("gen_embedding_backward_split_indice_weights_codegen_cuda.cu", src_cu)
    src_cu = template.render(dense=True)
    write("gen_embedding_backward_dense_indice_weights_codegen_cuda.cu", src_cu)


def backward_dense():
    generate(
        optimizer="dense",
        dense=True,
        args=make_args(
            [
                (FLOAT, "unused"),
            ]
        ),
    )


def gen__init__py():
    template = env.get_template("__init__.template")
    src_py = template.render()
    write("__init__.py", src_py)


def emb_codegen(install_dir=None, is_fbcode=True):
    if install_dir is not None and len(install_dir) != 0:
        args.install_dir = install_dir
    args.is_fbcode = is_fbcode
    adagrad()
    adam()
    approx_rowwise_adagrad()
    approx_sgd()
    backward_indices()
    backward_dense()
    forward_split()
    lamb()
    lars_sgd()
    partial_rowwise_adam()
    partial_rowwise_lamb()
    rowwise_adagrad()
    sgd()

    gen__init__py()


def main():
    emb_codegen()


if __name__ == "__main__":
    main()
