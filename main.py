import torch.multiprocessing as mp
from tensorboardX import SummaryWriter
import ctypes
import time
import trainers
import episodes
import models
import agents
import environment as env
import optimizers
import torch
from tqdm import tqdm
from utils.thordata_utils import get_scene_names
from utils.mean_calc import ScalarMeanTracker
import os
#TODO 可能要换A2C了
def main():
    #读取参数
    from demo_args import args
    #生成日志文件
    start_time = time.time()
    local_start_time_str = time.strftime(
        "%Y-%m-%d_%H-%M-%S", time.localtime(start_time)
    )

    if args.log_dir is not None:
        tb_log_dir = args.log_dir + "/" + args.log_title + "-" + local_start_time_str
        log_writer = SummaryWriter(log_dir=tb_log_dir)
    else:
        log_writer = SummaryWriter(comment=args.log_title)

    #确认gpu可用情况
    if args.gpu_ids == -1:
        args.gpu_ids = [-1]
    else:
        #torch.cuda.manual_seed(args.seed)
        assert torch.cuda.is_available()
        mp.set_start_method("spawn")

    #动态载入构造函数
    creator = {
        'model':getattr(models, args.model),
        'episode':getattr(episodes, args.episode),
        'agent':getattr(agents, args.agent),
        'env':getattr(env, args.env),
        'optimizer':getattr(optimizers, args.optimizer)
    }
    trainer = getattr(trainers, args.trainer)

    #生成全局模型并初始化优化算法
    shared_model = creator['model'](**args.model_args)
    if shared_model is not None:
        shared_model.share_memory()
        #optimizer.share_memory()
        print(shared_model)
    else:
        assert (
            args.agent == "RandomNavigationAgent"
        ), "The model is None but agent is not random agent"

    # 读取存档点，读取最新存档模型的参数到shared_model。其余线程会自动使用sync函数来同步
    if args.load_model_dir is not '':
        print("load %s"%args.load_model_dir)
        shared_model.load_state_dict(torch.load(args.load_model_dir))
    #生成各个线程
    processes = []

    end_flag = mp.Value(ctypes.c_bool, False)

    result_queue = mp.Queue()

    chosen_scene_names = get_scene_names(args.train_scenes)
    chosen_objects = []
    for k in args.train_targets.keys():
        chosen_objects = chosen_objects + args.train_targets[k]

    for thread_id in range(0, args.threads):
        if args.verbose:
            print('creating threads')
        p = mp.Process(
            target=trainer,
            args=(
                args,
                thread_id,
                result_queue,
                end_flag,
                shared_model,
                creator,
                chosen_scene_names,
                chosen_objects,
            ),
        )
        p.start()
        processes.append(p)
        time.sleep(0.1)
    print("Train agents created.")

    #取结果并记录
    print_freq = args.print_freq
    train_scalars = ScalarMeanTracker()

    train_total_ep = 0
    n_frames = 0

    pbar = tqdm(total=args.total_train_epi)

    try:
        while train_total_ep < args.total_train_epi:

            train_result = result_queue.get()
            train_scalars.add_scalars(train_result)
            train_total_ep += 1
            pbar.update(1)
            n_frames += train_result["ep_length"]
            if (train_total_ep % print_freq) == 0:
                log_writer.add_scalar("n_frames", n_frames, train_total_ep)
                tracked_means = train_scalars.pop_and_reset()
                for k in tracked_means:
                    log_writer.add_scalar(
                        k + "/train", tracked_means[k], train_total_ep
                    )

            if (train_total_ep % args.model_save_freq) == 0:
                #print(n_frames)
                if not os.path.exists(args.save_model_dir):
                    os.makedirs(args.save_model_dir)
                state_to_save = shared_model.state_dict()
                save_path = os.path.join(
                    args.save_model_dir,
                    "{0}_{1}_{2}.dat".format(
                        args.log_title, train_total_ep, local_start_time_str
                    ),
                )
                torch.save(state_to_save, save_path)

    finally:
        log_writer.close()
        end_flag.value = True
        for p in processes:
            time.sleep(0.1)
            p.join()
    pbar.close()

if __name__ == "__main__":
    main()

