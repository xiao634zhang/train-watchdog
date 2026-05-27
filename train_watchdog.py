#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练实验看门狗 (Train Watchdog)
================================
监控训练进程状态，发现异常时通过多种渠道发送告警。

支持的告警渠道（可扩展）：
  - 企业微信 Webhook
  - 飞书 Webhook
  - 钉钉 Webhook
  - 桌面弹窗 (notify-send)
  - 日志文件

支持检测的异常类型：
  1. 进程意外退出（进程数突然减少）
  2. GPU 显存异常（进程在但 GPU 无占用）
  3. GPU 利用率长时间为 0（可能死锁/hang）
  4. 训练日志出现错误关键词（Error/Exception/OOM/CUDA/Traceback）
  5. 编排脚本异常退出（如 train_bw_sweep.sh）
  6. 训练全部完成通知

用法：
  python train_watchdog.py [--config watchdog_config.json] [--interval 180]
"""

import os
import sys
import json
import time
import glob
import subprocess
import argparse
import logging
from datetime import datetime
from pathlib import Path

# ─── 配置 ────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    # 检查间隔（秒）
    "check_interval": 180,

    # 通知渠道配置
    "notify": {
        # 企业微信 Webhook（填入你的 webhook URL）
        "wecom": {
            "enabled": False,
            "webhook_url": ""
        },
        # 飞书 Webhook
        "feishu": {
            "enabled": False,
            "webhook_url": ""
        },
        # 钉钉 Webhook
        "dingtalk": {
            "enabled": False,
            "webhook_url": ""
        },
        # 桌面弹窗
        "desktop": {
            "enabled": True
        },
        # 日志文件（始终启用）
        "logfile": {
            "enabled": True,
            "path": "/home/user189/model_train/logs_bw_sweep/watchdog_alerts.log"
        }
    },

    # 监控目标
    "monitor": {
        # 匹配训练进程的关键词
        "process_pattern": "python.*train_yhk",
        # 监控的日志文件目录（glob 模式）
        "log_patterns": [
            "/home/user189/model_train/logs_bw_sweep/*.log",
            "/home/user189/model_train/logs/*.log"
        ],
        # 日志中的错误关键词
        "error_keywords": [
            "Error", "Exception", "Traceback", "OOM",
            "CUDA error", "RuntimeError", "out of memory",
            "Segmentation fault", "core dumped", "killed",
            "NaN", "inf loss"
        ],
        # 编排脚本进程匹配
        "orchestrator_pattern": "train_bw_sweep\\|train_vae_hist_param_sweep\\|train_labelclip",
        # GPU 利用率连续为 0 的次数阈值（超过则告警）
        "gpu_idle_threshold": 3,
    }
}

CONFIG_PATH = "/home/user189/model_train/watchdog/watchdog_config.json"

# ─── 日志设置 ─────────────────────────────────────────────────────────────

logger = logging.getLogger("watchdog")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_handler)

# ─── 通知渠道 ─────────────────────────────────────────────────────────────

def send_wecom(webhook_url: str, title: str, content: str):
    """企业微信 Webhook 通知"""
    import requests
    msg = {
        "msgtype": "markdown",
        "markdown": {
            "content": f"## 🚨 训练告警：{title}\n\n{content}\n\n> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        }
    }
    try:
        r = requests.post(webhook_url, json=msg, timeout=10)
        if r.status_code == 200:
            resp = r.json()
            if resp.get("errcode") == 0:
                logger.info(f"企业微信通知已发送: {title}")
            else:
                logger.warning(f"企业微信返回错误: {resp}")
        else:
            logger.warning(f"企业微信 HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"企业微信通知失败: {e}")


def send_feishu(webhook_url: str, title: str, content: str):
    """飞书 Webhook 通知"""
    import requests
    msg = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🚨 训练告警：{title}"},
                "template": "red"
            },
            "elements": [
                {"tag": "markdown", "content": content},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                ]}
            ]
        }
    }
    try:
        r = requests.post(webhook_url, json=msg, timeout=10)
        if r.status_code == 200:
            logger.info(f"飞书通知已发送: {title}")
        else:
            logger.warning(f"飞书 HTTP {r.status_code}: {r.text}")
    except Exception as e:
        logger.warning(f"飞书通知失败: {e}")


def send_dingtalk(webhook_url: str, title: str, content: str):
    """钉钉 Webhook 通知"""
    import requests
    msg = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"🚨 训练告警：{title}",
            "text": f"## 🚨 训练告警：{title}\n\n{content}\n\n---\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        }
    }
    try:
        r = requests.post(webhook_url, json=msg, timeout=10)
        if r.status_code == 200:
            logger.info(f"钉钉通知已发送: {title}")
        else:
            logger.warning(f"钉钉 HTTP {r.status_code}: {r.text}")
    except Exception as e:
        logger.warning(f"钉钉通知失败: {e}")


def send_desktop(title: str, content: str):
    """桌面弹窗通知"""
    try:
        # 截取前 200 字符避免弹窗太长
        short_content = content[:200].replace('\n', ' ')
        subprocess.run(
            ["notify-send", "-u", "critical", f"🚨 {title}", short_content],
            timeout=5, capture_output=True
        )
        logger.info(f"桌面通知已发送: {title}")
    except Exception as e:
        logger.warning(f"桌面通知失败: {e}")


def send_logfile(log_path: str, title: str, content: str):
    """写入告警日志文件"""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"🚨 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {title}\n")
        f.write(f"{'='*60}\n")
        f.write(content + "\n")


def send_alert(config: dict, title: str, content: str, level: str = "error"):
    """统一告警发送接口"""
    notify_cfg = config["notify"]

    # 日志文件（始终写入）
    if notify_cfg["logfile"]["enabled"]:
        send_logfile(notify_cfg["logfile"]["path"], title, content)

    # 桌面弹窗
    if notify_cfg["desktop"]["enabled"]:
        send_desktop(title, content)

    # 企业微信
    if notify_cfg["wecom"]["enabled"] and notify_cfg["wecom"]["webhook_url"]:
        send_wecom(notify_cfg["wecom"]["webhook_url"], title, content)

    # 飞书
    if notify_cfg["feishu"]["enabled"] and notify_cfg["feishu"]["webhook_url"]:
        send_feishu(notify_cfg["feishu"]["webhook_url"], title, content)

    # 钉钉
    if notify_cfg["dingtalk"]["enabled"] and notify_cfg["dingtalk"]["webhook_url"]:
        send_dingtalk(notify_cfg["dingtalk"]["webhook_url"], title, content)


# ─── 监控检查函数 ─────────────────────────────────────────────────────────

def get_train_processes(pattern: str) -> list:
    """获取当前训练进程列表"""
    try:
        result = subprocess.run(
            ["bash", "-c", f"ps aux --no-headers | grep '{pattern}' | grep -v grep"],
            capture_output=True, text=True, timeout=10
        )
        lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
        processes = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 11:
                processes.append({
                    "pid": parts[1],
                    "cpu": parts[2],
                    "mem": parts[3],
                    "cmd": " ".join(parts[10:])
                })
        return processes
    except Exception:
        return []


def get_gpu_status() -> list:
    """获取 GPU 状态"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        gpus = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 5:
                    gpus.append({
                        "index": int(parts[0]),
                        "util": int(parts[1]),
                        "mem_used": int(parts[2]),
                        "mem_total": int(parts[3]),
                        "temp": int(parts[4])
                    })
        return gpus
    except Exception:
        return []


def get_gpu_processes() -> dict:
    """获取每个 GPU 上运行的进程"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,gpu_bus_id,used_memory",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10
        )
        gpu_procs = {}
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 1:
                    gpu_procs[parts[0]] = True
        return gpu_procs
    except Exception:
        return {}


def check_log_errors(log_patterns: list, error_keywords: list, last_positions: dict) -> list:
    """检查日志文件中是否出现新的错误"""
    alerts = []
    for pattern in log_patterns:
        for log_file in glob.glob(pattern):
            file_size = os.path.getsize(log_file)
            last_pos = last_positions.get(log_file, 0)

            if file_size <= last_pos:
                continue

            try:
                with open(log_file, 'r', errors='ignore') as f:
                    f.seek(last_pos)
                    new_content = f.read()
                    last_positions[log_file] = f.tell()

                for keyword in error_keywords:
                    if keyword.lower() in new_content.lower():
                        # 找到包含关键词的行
                        error_lines = []
                        for line in new_content.split('\n'):
                            if keyword.lower() in line.lower():
                                error_lines.append(line.strip())
                        if error_lines:
                            # 最多取 5 行
                            sample = '\n'.join(error_lines[:5])
                            alerts.append({
                                "file": os.path.basename(log_file),
                                "keyword": keyword,
                                "sample": sample
                            })
                        break  # 每个文件只报告第一个匹配的关键词
            except Exception:
                pass

    return alerts


def check_orchestrator(pattern: str) -> bool:
    """检查编排脚本是否还在运行"""
    try:
        result = subprocess.run(
            ["bash", "-c", f"ps aux --no-headers | grep -E '{pattern}' | grep -v grep | wc -l"],
            capture_output=True, text=True, timeout=10
        )
        return int(result.stdout.strip()) > 0
    except Exception:
        return False


# ─── 主监控循环 ───────────────────────────────────────────────────────────

class TrainWatchdog:
    def __init__(self, config: dict):
        self.config = config
        self.monitor_cfg = config["monitor"]

        # 状态追踪
        self.prev_process_count = None
        self.prev_pids = set()
        self.gpu_idle_counts = {}  # gpu_index -> 连续空闲次数
        self.last_log_positions = {}
        self.had_processes = False  # 是否曾经有过训练进程
        self.orchestrator_was_running = False
        self.alerted_pids = set()  # 已经告警过的退出 PID
        self.all_done_notified = False

    def check_once(self):
        """执行一轮检查"""
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        processes = get_train_processes(self.monitor_cfg["process_pattern"])
        gpus = get_gpu_status()
        current_pids = {p["pid"] for p in processes}
        current_count = len(processes)

        # ── 1. 检查进程意外退出 ──────────────────────────────────
        if self.prev_process_count is not None and current_count < self.prev_process_count:
            exited_pids = self.prev_pids - current_pids
            new_exits = exited_pids - self.alerted_pids

            if new_exits and current_count > 0:
                # 还有进程在跑，但有些退了 → 可能是异常
                self.alerted_pids.update(new_exits)
                content = (
                    f"进程数从 {self.prev_process_count} 减少到 {current_count}\n"
                    f"退出的 PID: {', '.join(new_exits)}\n\n"
                    f"仍在运行的进程:\n"
                )
                for p in processes:
                    content += f"  PID={p['pid']} CMD={p['cmd']}\n"
                send_alert(self.config, "训练进程意外退出", content)

        # ── 2. 检查 GPU 利用率异常 ──────────────────────────────
        if current_count > 0 and gpus:
            # 找出哪些 GPU 应该在工作（根据进程的 cmd 参数推断）
            active_gpus = set()
            for p in processes:
                # 从命令行参数中提取 GPU ID（第一个数字参数）
                cmd_parts = p["cmd"].split()
                for i, part in enumerate(cmd_parts):
                    if part == "train_yhk.py" and i + 1 < len(cmd_parts):
                        try:
                            gpu_id = int(cmd_parts[i + 1])
                            active_gpus.add(gpu_id)
                        except ValueError:
                            pass

            for gpu in gpus:
                idx = gpu["index"]
                if idx in active_gpus:
                    if gpu["util"] == 0 and gpu["mem_used"] < 100:
                        self.gpu_idle_counts[idx] = self.gpu_idle_counts.get(idx, 0) + 1
                        threshold = self.monitor_cfg["gpu_idle_threshold"]
                        if self.gpu_idle_counts[idx] == threshold:
                            content = (
                                f"GPU {idx} 连续 {threshold} 次检查利用率为 0%\n"
                                f"显存: {gpu['mem_used']} MiB / {gpu['mem_total']} MiB\n"
                                f"温度: {gpu['temp']}°C\n\n"
                                f"该 GPU 上应有训练进程在运行，可能出现了死锁/hang"
                            )
                            send_alert(self.config, f"GPU {idx} 疑似 hang", content)
                    else:
                        self.gpu_idle_counts[idx] = 0

        # ── 3. 检查日志错误 ──────────────────────────────────────
        log_alerts = check_log_errors(
            self.monitor_cfg["log_patterns"],
            self.monitor_cfg["error_keywords"],
            self.last_log_positions
        )
        for alert in log_alerts:
            content = (
                f"日志文件: {alert['file']}\n"
                f"匹配关键词: {alert['keyword']}\n\n"
                f"错误内容:\n{alert['sample']}"
            )
            send_alert(self.config, f"训练日志出现 {alert['keyword']}", content)

        # ── 4. 检查编排脚本状态 ──────────────────────────────────
        orch_running = check_orchestrator(self.monitor_cfg["orchestrator_pattern"])
        if self.orchestrator_was_running and not orch_running and current_count > 0:
            content = (
                f"编排脚本已退出，但仍有 {current_count} 个训练进程在运行\n"
                f"这可能意味着编排脚本异常退出（如 set -e 导致的错误）\n"
                f"后续批次的实验可能不会被启动！"
            )
            send_alert(self.config, "编排脚本异常退出", content)
        elif self.orchestrator_was_running and not orch_running and current_count == 0:
            # 编排脚本和训练都结束了 — 可能是正常完成，也可能是崩溃
            pass
        self.orchestrator_was_running = orch_running

        # ── 5. 检查训练全部完成 ──────────────────────────────────
        if self.had_processes and current_count == 0 and not self.all_done_notified:
            self.all_done_notified = True
            # 收集 GPU 状态
            gpu_info = ""
            for gpu in gpus:
                gpu_info += f"  GPU {gpu['index']}: {gpu['util']}%, {gpu['mem_used']} MiB, {gpu['temp']}°C\n"
            content = (
                f"所有训练进程已结束！\n\n"
                f"GPU 状态:\n{gpu_info}\n"
                f"请检查训练结果是否正常。"
            )
            send_alert(self.config, "✅ 训练全部完成", content, level="info")

        # 更新状态
        if current_count > 0:
            self.had_processes = True
            self.all_done_notified = False  # 如果新实验开始，重置
        self.prev_process_count = current_count
        self.prev_pids = current_pids

        logger.info(f"检查完成: {current_count} 个训练进程, {len(gpus)} 个 GPU")

    def run(self):
        """主循环"""
        interval = self.config["check_interval"]
        logger.info(f"🐕 训练看门狗已启动，检查间隔: {interval}s")
        logger.info(f"通知渠道: {', '.join(k for k, v in self.config['notify'].items() if v.get('enabled'))}")

        # 初始状态
        processes = get_train_processes(self.monitor_cfg["process_pattern"])
        self.prev_process_count = len(processes)
        self.prev_pids = {p["pid"] for p in processes}
        if self.prev_process_count > 0:
            self.had_processes = True
        self.orchestrator_was_running = check_orchestrator(self.monitor_cfg["orchestrator_pattern"])

        logger.info(f"初始状态: {self.prev_process_count} 个训练进程")

        while True:
            try:
                time.sleep(interval)
                self.check_once()
            except KeyboardInterrupt:
                logger.info("看门狗收到 Ctrl+C，退出")
                break
            except Exception as e:
                logger.error(f"看门狗异常: {e}", exc_info=True)
                time.sleep(60)


# ─── 配置管理 ─────────────────────────────────────────────────────────────

def load_config(config_path: str) -> dict:
    """加载配置文件，不存在则创建默认配置。环境变量可覆盖 webhook URL。"""
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            user_config = json.load(f)
        # 合并默认配置（用户配置优先）
        config = DEFAULT_CONFIG.copy()
        _deep_merge(config, user_config)
    else:
        # 创建默认配置文件
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        logger.info(f"已创建默认配置文件: {config_path}")
        logger.info("请编辑配置文件设置 webhook URL，然后重新启动")
        return DEFAULT_CONFIG

    # 环境变量覆盖 webhook URL（避免将密钥写入配置文件）
    env_map = {
        "wecom": "WECOM_WEBHOOK_URL",
        "feishu": "FEISHU_WEBHOOK_URL",
        "dingtalk": "DINGTALK_WEBHOOK_URL",
    }
    for channel, env_var in env_map.items():
        url = os.environ.get(env_var, "")
        if url:
            config["notify"][channel]["webhook_url"] = url
            if not config["notify"][channel]["enabled"]:
                config["notify"][channel]["enabled"] = True

    return config


def _deep_merge(base: dict, override: dict):
    """递归合并字典"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ─── 入口 ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="训练实验看门狗")
    parser.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--interval", type=int, help="检查间隔（秒），覆盖配置文件")
    parser.add_argument("--test", action="store_true", help="发送测试通知后退出")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.interval:
        config["check_interval"] = args.interval

    if args.test:
        logger.info("发送测试通知...")
        send_alert(config, "🧪 测试通知", "这是一条测试消息，如果你收到了说明通知渠道配置正确！")
        logger.info("测试完成")
        return

    watchdog = TrainWatchdog(config)
    watchdog.run()


if __name__ == "__main__":
    main()
