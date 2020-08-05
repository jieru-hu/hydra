# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

# This file is to be rsynced to ray cluster and invoke on the cluster.
import logging
import os
import sys
import urllib.request
from pathlib import Path
from typing import List

import ray
import ray.cloudpickle as cloudpickle
from hydra.core.hydra_config import HydraConfig
from hydra.core.singleton import Singleton
from hydra.core.utils import JobReturn, setup_globals
from omegaconf import open_dict

from hydra_plugins.hydra_ray_launcher._launcher_util import (
    JOB_RETURN_PICKLE,
    JOB_SPEC_PICKLE,
    launch_job_on_ray,
    start_ray,
)

log = logging.getLogger(__name__)


def launch_jobs(temp_dir: str) -> None:
    runs = []
    with open(os.path.join(temp_dir, JOB_SPEC_PICKLE), "rb") as f:
        job_spec = cloudpickle.load(f)
        singleton_state = job_spec["singleton_state"]
        sweep_configs = job_spec["sweep_configs"]
        task_function = job_spec["task_function"]

        instance_id = _get_instance_id()

        sweep_dir = None

        for sweep_config in sweep_configs:
            with open_dict(sweep_config):
                sweep_config.hydra.job.id = (
                    f"{instance_id}_{sweep_config.hydra.job.num}"
                )
            setup_globals()
            Singleton.set_state(singleton_state)
            HydraConfig.instance().set_config(sweep_config)
            ray_init_cfg = HydraConfig.get().launcher.params.ray_init_cfg
            ray_remote_cfg = HydraConfig.get().launcher.params.ray_remote_cfg

            if not sweep_dir:
                sweep_dir = Path(str(HydraConfig.get().sweep.dir))
                sweep_dir.mkdir(parents=True, exist_ok=True)
                os.chdir(sweep_dir)

            start_ray(ray_init_cfg)
            ray_obj = launch_job_on_ray(
                ray_remote_cfg, sweep_config, task_function, singleton_state
            )
            runs.append(ray_obj)

    result = [ray.get(run) for run in runs]
    _dump_job_return(result, temp_dir)


def _dump_job_return(result: List[JobReturn], tmp_dir: str) -> None:
    path = os.path.join(tmp_dir, JOB_RETURN_PICKLE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        cloudpickle.dump(result, f)
    log.info(f"Pickle for job returns: {f.name}")


def _get_instance_id() -> str:
    return (
        urllib.request.urlopen("http://169.254.169.254/latest/meta-data/instance-id")
        .read()
        .decode()
    )


if __name__ == "__main__":
    launch_jobs(sys.argv[1])