"""主程序入口"""

import logging
import os
import platform
import signal
import sys

from logging.handlers import TimedRotatingFileHandler

from src.robot import Concerto

robot = Concerto()

# 检测运行平台是否为Linux，导入readline模块实现TAB补全和历史命令读取
if platform.system() == "Linux":
    import readline # pylint: disable=import-error

    def completer(text, state):
        """命令自动补全"""
        options = [cmd for cmd in robot.cmd if cmd.startswith(text)]
        if state < len(options):
            return options[state]
        else:
            return None

    readline.parse_and_bind("tab: complete")
    readline.set_completer(completer)

def my_handler(signum, frame): # pylint: disable=unused-argument
    """捕获Ctrl C信号"""
    robot.stop()
    sys.exit()

signal.signal(signal.SIGINT, my_handler)

# 日志记录
logger = logging.getLogger()
logger.setLevel(logging.INFO)

os.makedirs(robot.config.log_path, exist_ok=True)
handler = TimedRotatingFileHandler(
    os.path.join(robot.config.log_path, "bot.log"),
    when="midnight",
    interval=1,
    encoding="utf-8"
)
formatter = logging.Formatter("%(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# 启动主函数
if __name__ == "__main__":
    robot.run()
