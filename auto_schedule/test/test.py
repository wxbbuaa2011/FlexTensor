import tvm
import torch
import numpy as np
import os
import logging
from auto_schedule.models import OpScheduleCPUd5
from auto_schedule.examples import FUNC_TABLE
from auto_schedule.scheduler import op_schedule_cpu_general_dx, evaluate, graph_schedule_cpu_general_dx


CPU_NUM = os.cpu_count()


def test_feature_extract():
    for name, compute in FUNC_TABLE.items():
        print("#################################")
        print(name)
        func = compute.func
        args = compute.args
        ops, bufs = func(*args)
        target = Target("llvm")
        s = Schedule(ops, target)
        for op, sch in s.op_schedule_dict.items():
            print(op)
            print("shape=", sch.shape)
            print("is_compute=", sch.is_compute)
            print("has_reduce=", sch.has_reduce)
            print("able_inline=", sch.able_inline)
            print("is_output=", sch.is_output)
            print("next_num=", sch.next_num)
            print("num_outputs=", sch.num_outputs)
            print("output_tensors=", sch.output_tensors)
            print("num_inputs=", sch.num_inputs)
            print("input_tensors=", sch.input_tensors)
            print("spatial_iter_var=", sch.org_spatial_iter_var_names)
            print("reduce_iter_var=", sch.org_reduce_iter_var_names)
            print("iter_var_feature=")
            for var_name, fea in sch.iter_var_feature_dict.items():
                print("    ", var_name, fea)
            print("visited_by=")
            for vop, vfea in sch.visit_feature.items():
                print("    ", vop)
                for (d, v) in vfea:
                    print("    ", v)
                    for name, l in d.items():
                        print("    ", name, l)
            print()


def test_op_schedule():
    for name, compute in FUNC_TABLE.items():
        if name in ["conv3d", "conv3d_batch", "conv3d_channel", "conv3d_channel_batch"]:
            continue
        print("#################################")
        print(name)
        func = compute.func
        args = compute.args
        print(args)
        op, bufs = func(*args)
        dim = 5
        model = OpScheduleCPUd5(3, 128)
        s = tvm.create_schedule(op)
        # if torch.cuda.is_available():
        #     device= torch.device("cuda:0")
        #     model.cuda(device)
        # else:
        #     device = torch.device("cpu:0")

        def count_parameters(model):
            return sum(p.numel() for p in model.parameters() if p.requires_grad)
        print(count_parameters(model))
        device = torch.device("cpu:0")
        lst, diary = op_schedule_cpu_general_dx(dim, s, op, model, device)
        print(tvm.lower(s, bufs, simple_mode=True))
        f = tvm.build(s, bufs, "llvm")
        cost = evaluate(s, bufs, "llvm", np.random.randint(0, CPU_NUM))
        msg = "{}({}) is {}ms, pass!\n".format(name, args, cost)
        print(msg)
        for ele in diary:
            print(ele)


def robust_test(round=100):
    for i in range(round):
        for name, compute in FUNC_TABLE.items():
            if name in ["conv3d", "conv3d_batch", "conv3d_channel", "conv3d_channel_batch"]:
                continue
            func = compute.func
            args = compute.args
            op, bufs = func(*args)
            dim = 5
            model = OpScheduleCPUd5(3, 32)
            s = tvm.create_schedule(op)
            lst, diary = op_schedule_cpu_general_dx(dim, s, op, model, random=True)
            try:
                stmt = tvm.lower(s, bufs, simple_mode=True)
                f = tvm.build(s, bufs, "llvm")
                cost = evaluate(s, bufs, "llvm", np.random.randint(0, CPU_NUM), timeout=0.003)
                msg = "{}({}) is {}ms, pass!\n".format(name, args, cost)
            except Exception as e:
                msg = "{}({}) error!\n{}\n{}\n".format(name, args, str(e), diary)
            with open("test_log.log", "a") as f:
                f.write(msg)


def test_graph_schedule_cpu_general_dx(func, args, model_path, random=False, sampling=False, timeout=10.0):
    print("#################################")
    ops, bufs = func(*args)
    dim = 5
    s = tvm.create_schedule(ops)
    graph_schedule_cpu_general_dx(dim, s, ops, model_path, random=random, sampling=sampling)
    f = tvm.build(s, bufs, "llvm")
    cost = evaluate(s, bufs, "llvm", np.random.randint(0, CPU_NUM), number=10, timeout=timeout)
    msg = "Optimial time cost is {}ms, pass!\n".format(cost)
    print(msg)


def test_graph_schedule_gpu_specific_any(number, type="cuda"):
    pass_logger = logging.getLogger("pass")
    wrong_logger = logging.getLogger("wrong")
    pass_logger.setLevel(logging.INFO)
    wrong_logger.setLevel(logging.INFO)
    pass_fh = logging.FileHandler("passed_test.log")
    wrong_fh = logging.FileHandler("wrong_test.log")
    pass_logger.addHandler(pass_fh)
    wrong_logger.addHandler(wrong_fh)

    matmul = FUNC_TABLE["matmul_batch"].func
    matmul_args = (10, 100, 100, 100)
    conv2d = FUNC_TABLE["conv2d_channel_batch"].func
    conv_args = (256, 14, 14, 3, 1, 1, 3, 1, 0)
    func_lst = [matmul, conv2d]
    args_lst = [matmul_args, conv_args]
    for j in range(number):
        for i in range(2):
            func = func_lst[i]
            args = args_lst[i]
            ops, bufs = func(*args)
            model = ScheduleModel(2, 2, random=True)
            if type == "cuda":
                target = Target("cuda", bx=4, by=4, bz=4, tx=128, ty=32, tz=32)
                target.turn_on('support_bind')
            elif type == "llvm":
                target = Target("llvm")
            else:
                raise ValueError("not support type: {}".format(type))
            sch, s, improve = graph_schedule_gpu_specific_any(ops, target, model)
            try:
                time_cost = evaluate(sch, bufs, type, 1)
                pass_logger.info("{}{} passed with time cost={}ms".format(func.__name__, args, time_cost))
            except Exception as e:
                stmt = tvm.lower(sch, bufs, simple_mode=True)
                func = tvm.build(sch, bufs, "cuda")
                msg = str(e)
                schedule_diary = s.schedule_diary
                wrong_logger.info("#################################")
                wrong_logger.info(str(stmt))
                wrong_logger.info(str(func.imported_modules[0].get_source()))
                wrong_logger.info(msg)
                for line in schedule_diary:
                    wrong_logger.info("{}\n".format(line))



