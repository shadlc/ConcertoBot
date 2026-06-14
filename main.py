"""主程序入口"""

from src.robot import Concerto


if __name__ == "__main__":
    robot = Concerto()
    robot.setup_runtime()
    robot.run()
