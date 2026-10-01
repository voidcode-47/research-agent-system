"""工具模块。"""
from tools.base import BaseTool, TOOL_REGISTRY, register_tool, get_tool, list_tools, get_tools_schema
from tools.search_tool import DuckDuckGoSearchTool
from tools.web_search_cn import WebSearchCNTool
from tools.academic_search import AcademicSearchTool
from tools.web_scraper import WebScraperTool
from tools.pdf_parser import PDFParserTool

# VectorStore 延迟导入，避免导入 tools 包时强制依赖 chromadb
def __getattr__(name):
    if name == "VectorStore":
        from tools.vector_store import VectorStore
        return VectorStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "BaseTool", "TOOL_REGISTRY", "register_tool", "get_tool", "list_tools", "get_tools_schema",
    "DuckDuckGoSearchTool", "WebSearchCNTool", "AcademicSearchTool",
    "WebScraperTool", "PDFParserTool", "VectorStore",
]
