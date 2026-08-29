"""computer_tools 包（Go 宿主兼容运行时，对齐本体 computer_tools 导出）。"""

from astrbot.core.tools.computer_tools.cua import (  # noqa: F401
    CuaKeyboardTypeTool,
    CuaMouseClickTool,
    CuaScreenshotTool,
)
from astrbot.core.tools.computer_tools.fs import (  # noqa: F401
    FileDownloadTool,
    FileEditTool,
    FileReadTool,
    FileUploadTool,
    FileWriteTool,
    GrepTool,
)
from astrbot.core.tools.computer_tools.python import (  # noqa: F401
    LocalPythonTool,
    PythonTool,
)
from astrbot.core.tools.computer_tools.shell import (  # noqa: F401
    ExecuteShellTool,
    LocalExecuteShellTool,
    ShellSessionTool,
)
from astrbot.core.tools.computer_tools.shipyard_neo import (  # noqa: F401
    AnnotateExecutionTool,
    BrowserBatchExecTool,
    BrowserExecTool,
    CreateSkillCandidateTool,
    CreateSkillPayloadTool,
    EvaluateSkillCandidateTool,
    GetExecutionHistoryTool,
    GetSkillPayloadTool,
    ListSkillCandidatesTool,
    ListSkillReleasesTool,
    PromoteSkillCandidateTool,
    RollbackSkillReleaseTool,
    RunBrowserSkillTool,
    SyncSkillReleaseTool,
)
from astrbot.core.tools.computer_tools.util import (  # noqa: F401
    check_admin_permission,
    normalize_umo_for_workspace,
)

__all__ = [
    "AnnotateExecutionTool",
    "BrowserBatchExecTool",
    "BrowserExecTool",
    "CreateSkillCandidateTool",
    "CreateSkillPayloadTool",
    "CuaKeyboardTypeTool",
    "CuaMouseClickTool",
    "CuaScreenshotTool",
    "EvaluateSkillCandidateTool",
    "ExecuteShellTool",
    "FileDownloadTool",
    "FileEditTool",
    "FileReadTool",
    "FileUploadTool",
    "FileWriteTool",
    "GetExecutionHistoryTool",
    "GetSkillPayloadTool",
    "GrepTool",
    "ListSkillCandidatesTool",
    "ListSkillReleasesTool",
    "LocalExecuteShellTool",
    "LocalPythonTool",
    "PromoteSkillCandidateTool",
    "PythonTool",
    "RollbackSkillReleaseTool",
    "RunBrowserSkillTool",
    "ShellSessionTool",
    "SyncSkillReleaseTool",
    "check_admin_permission",
    "normalize_umo_for_workspace",
]