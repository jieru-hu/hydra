# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

"""
Run this script with AWS admin creds to create new Ray AMIs if any hydra-core & hydra-ray-launcher
dependencies changes.
Then update env variable with the new AMI.
"""
import os
import subprocess
import tempfile
import time
from datetime import datetime

import boto3
from omegaconf import OmegaConf


def _run_command(command: str) -> str:
    print(f"{str(datetime.now())} - Running: {command}")
    output = subprocess.getoutput(command)
    print(f"{str(datetime.now())} - {output}")
    return output


def set_up_machine() -> None:
    security_group_id = os.environ.get("RAY_BUILD_AMI_SEC_GROUP", "")
    assert security_group_id != "", "Security group cannot be empty!"

    # set up security group rules to allow pip install
    _run_command(
        f"aws ec2 authorize-security-group-egress --group-id {security_group_id} "
        f"--ip-permissions IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{{CidrIp=0.0.0.0/0}}]"
    )

    yaml = "ray_cluster.yaml"

    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
        with open(f.name, "w") as file:
            OmegaConf.save(config=OmegaConf.load(yaml), f=file.name, resolve=True)
        yaml = f.name
        _run_command(f"ray up {yaml} -y")
        _run_command(f"ray rsync_up {yaml} './setup_ami.py' '/home/ubuntu/' ")
        _run_command(f"ray rsync_up {yaml} '../requirements.txt' '/home/ubuntu/' ")

        print(
            "Installing dependencies now, this may take a while (very likely more than 20 mins) ..."
        )
        _run_command(f"ray exec {yaml} 'python ./setup_ami.py' ")

        # remove security group egress rules
        _run_command(
            f"aws ec2 revoke-security-group-egress --group-id {security_group_id} "
            f"--ip-permissions IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{{CidrIp=0.0.0.0/0}}]"
        )

        # export the instance to an AMI
        ec2_resource = boto3.resource("ec2")
        ec2_client = boto3.client("ec2")
        instance_id = None
        for instance in ec2_resource.instances.all():
            for t in instance.tags:
                if (
                    t["Key"] == "Name"
                    and t["Value"] == "ray-ray_test_base_AMI-head"
                    and instance.state["Name"] == "running"
                ):
                    instance_id = instance.id
                    break

        assert instance_id is not None

        ami_name = (
            f"ray_test_ami_{str(datetime.now()).replace(' ', '_').replace(':', '.')}"
        )

        ret = ec2_client.create_image(InstanceId=instance_id, Name=ami_name)
        image_id = ret.get("ImageId")

        # wait till image is ready, 30 mins for now, this could take a while
        for i in range(60):
            time.sleep(30)
            image = ec2_resource.Image(image_id)
            if image.state == "available":
                print(
                    f"{image} ready for use now, pls update your env variable: AWS_RAY_AMI={image}. "
                    f"Please also update the test user's IAM policy to allow access to the new AMI."
                )
                break
            else:
                print(f"{image} current state {image.state}")

        # Terminate instance now we have the AMI.
        ec2_resource.instances.filter(InstanceIds=[instance_id]).terminate()


if __name__ == "__main__":
    set_up_machine()
