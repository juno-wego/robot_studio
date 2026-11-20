"""Project the WeGo MID360 cloud and map with SLAM Toolbox."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def cloud_projection(cloud_topic):
    return Node(
        package="pointcloud_to_laserscan",
        executable="pointcloud_to_laserscan_node",
        name="wego_scan_projection",
        remappings=[("cloud_in", cloud_topic), ("scan", "/wego_scan")],
        parameters=[{
            "target_frame": "base_link",
            "transform_tolerance": 0.10,
            "min_height": -0.08,
            "max_height": 0.55,
            "angle_min": -3.14159265,
            "angle_max": 3.14159265,
            "angle_increment": 0.01745329,
            "scan_time": 0.10,
            "range_min": 0.35,
            "range_max": 12.0,
            "use_inf": True,
            "use_sim_time": LaunchConfiguration("use_sim_time"),
        }],
        output="screen",
    )


def generate_launch_description():
    assets = Path(__file__).resolve().parent
    slam_launch = Path(get_package_share_directory("slam_toolbox")) / "launch/online_async_launch.py"
    return LaunchDescription([
        DeclareLaunchArgument("cloud_topic", default_value="/livox/lidar"),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("slam_params_file", default_value=str(assets / "slam_params.yaml")),
        cloud_projection(LaunchConfiguration("cloud_topic")),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(slam_launch)),
            launch_arguments={
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "slam_params_file": LaunchConfiguration("slam_params_file"),
            }.items(),
        ),
    ])
