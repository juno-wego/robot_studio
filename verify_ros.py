"""Passive ROS 2 topic acceptance check. Runs on the booted Jetson."""

import argparse
import json
import os
from pathlib import Path
import re
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    seconds = spec.get("seconds", 10)
    topics = spec.get("topics")
    if type(seconds) is not int or not 3 <= seconds <= 60 or not isinstance(topics, list) or not 1 <= len(topics) <= 30:
        raise ValueError("검수 시간은 3~60초, 토픽 수는 1~30개로 설정하세요.")
    names = [entry["topic"] for entry in topics]
    if len(set(names)) != len(names) or not all(re.fullmatch(r"/(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*", name, re.ASCII) for name in names):
        raise ValueError("중복되지 않는 ROS 토픽 이름을 사용하세요. 예: /scan")
    for item in topics:
        required = item.get("min_count", 1)
        if type(required) is not int or not 1 <= required <= 20000:
            raise ValueError("토픽별 최소 수신 횟수는 1~20000입니다.")

    if "domain" in spec:
        domain = spec["domain"]
        if type(domain) is not int or not 0 <= domain <= 232:
            raise ValueError("DDS 도메인은 0~232 값을 사용하세요.")
        os.environ["ROS_DOMAIN_ID"] = str(domain)
    interface = spec.get("interface", "")
    if interface:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,14}", interface) or not (
            Path("/sys/class/net") / interface
        ).is_dir():
            raise ValueError("Jetson의 ROS 통신 포트 이름을 확인하세요.")
        os.environ["RMW_IMPLEMENTATION"] = "rmw_cyclonedds_cpp"
        os.environ["CYCLONEDDS_URI"] = (
            "<CycloneDDS><Domain><General><Interfaces>"
            f'<NetworkInterface name="{interface}" />'
            "</Interfaces></General></Domain></CycloneDDS>"
        )

    import rclpy
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from rosidl_runtime_py.utilities import get_message

    rclpy.init()
    node = rclpy.create_node("wego_install_verify", enable_rosout=False,
                            start_parameter_services=False)
    subscriptions = {}
    stats = {name: {"count": 0, "last": None} for name in names}
    errors = {}
    start = time.monotonic()

    def on_message(name, message):
        info = stats[name]
        info["count"] += 1
        # Sample only small fields. Never retain point clouds, images or blobs.
        if info["last"] is None:
            details = {}
            if hasattr(message, "error_code"):
                details["error_code"] = message.error_code
            if hasattr(message, "mode"):
                details["mode"] = message.mode
            if hasattr(message, "width") and hasattr(message, "height"):
                details.update(width=message.width, height=message.height)
            if hasattr(message, "ranges"):
                details["ranges"] = len(message.ranges)
            if hasattr(message, "header") and hasattr(message.header, "frame_id"):
                details["frame_id"] = message.header.frame_id
            info["last"] = details

    print(f"{seconds}초간 ROS 토픽 수신 확인", flush=True)
    try:
        while time.monotonic() - start < seconds:
            discover = dict(node.get_topic_names_and_types())
            for entry in topics:
                name = entry["topic"]
                if name in subscriptions:
                    continue
                offered = discover.get(name, [])
                expected = entry.get("type", "")
                if not offered:
                    continue
                if expected and expected not in offered:
                    errors[name] = "메시지 형식 불일치"
                    continue
                if not expected and len(offered) != 1:
                    errors[name] = "메시지 형식을 지정하세요"
                    continue
                try:
                    message_type = get_message(expected or offered[0])
                    subscriptions[name] = node.create_subscription(
                        message_type, name, lambda msg, topic=name: on_message(topic, msg),
                        QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT,
                                   durability=DurabilityPolicy.VOLATILE))
                    errors.pop(name, None)
                except (ImportError, AttributeError, ValueError, TypeError) as error:
                    errors[name] = str(error)
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()

    ok = True
    for entry in topics:
        name = entry["topic"]
        count = stats[name]["count"]
        minimum = entry.get("min_count", 1)
        passed = count >= minimum
        ok &= passed
        details = json.dumps(stats[name]["last"] or {}, ensure_ascii=False)
        print(f"{'수신 확인' if passed else '확인 필요'}  {name}  {count}/{minimum}  {errors.get(name, details)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
