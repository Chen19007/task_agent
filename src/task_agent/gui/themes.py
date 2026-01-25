"""颜色主题配置

定义 Dear PyGui 使用的颜色方案，参考 Rich 的主题风格。
"""


class ThemeColors:
    """颜色主题（RGB 0-255）"""

    # 角色颜色
    USER_TEXT = (200, 200, 200)
    ASSISTANT_TEXT = (100, 200, 255)
    SYSTEM_TEXT = (150, 150, 150)

    # Tool Tag 颜色
    PS_CALL_TEXT = (255, 200, 100)
    PS_CALL_BG = (80, 40, 40)

    BUILTIN_TEXT = (255, 200, 100)

    CREATE_AGENT_TEXT = (150, 255, 150)
    CREATE_AGENT_BG = (40, 80, 40)

    THINK_TEXT = (200, 200, 200)
    THINK_BG = (50, 50, 60)

    PS_CALL_RESULT_TEXT = (200, 200, 200)
    PS_CALL_RESULT_BG = (50, 50, 70)

    # 界面颜色
    WINDOW_BG = (30, 30, 35)
    PANEL_BG = (40, 40, 45)
    BORDER_COLOR = (60, 60, 70)
    INPUT_BG = (50, 50, 55)
    BUTTON_HOVER = (70, 70, 80)
    BUTTON_ACTIVE = (60, 100, 60)

    # 对话框颜色
    COMMAND_TEXT = (255, 220, 150)
    HINT_TEXT = (180, 180, 180)

    @classmethod
    def get_tag_color(cls, tag_type: str) -> tuple:
        """根据标签类型返回颜色"""
        color_map = {
            "ps_call": cls.PS_CALL_TEXT,
            "builtin": cls.BUILTIN_TEXT,
            "create_agent": cls.CREATE_AGENT_TEXT,
            "think": cls.THINK_TEXT,
            "ps_call_result": cls.PS_CALL_RESULT_TEXT,
        }
        return color_map.get(tag_type, cls.USER_TEXT)

    @classmethod
    def get_role_color(cls, role: str) -> tuple:
        """根据角色返回颜色"""
        color_map = {
            "user": cls.USER_TEXT,
            "assistant": cls.ASSISTANT_TEXT,
            "system": cls.SYSTEM_TEXT,
        }
        return color_map.get(role, cls.USER_TEXT)
