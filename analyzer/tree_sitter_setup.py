"""
tree_sitter_setup.py
Initializes and returns the tree-sitter PHP parser.
Versions locked: tree-sitter==0.23.2, tree-sitter-php==0.23.11
"""

from tree_sitter import Language, Parser
import tree_sitter_php as ts_php

# Module-level singletons — parse once, reuse everywhere
_PHP_LANGUAGE: Language = None
_PARSER: Parser = None


def get_parser() -> Parser:
    global _PHP_LANGUAGE, _PARSER
    if _PARSER is None:
        _PHP_LANGUAGE = Language(ts_php.language_php())
        _PARSER = Parser(_PHP_LANGUAGE)
    return _PARSER


def get_language() -> Language:
    global _PHP_LANGUAGE
    if _PHP_LANGUAGE is None:
        _PHP_LANGUAGE = Language(ts_php.language_php())
    return _PHP_LANGUAGE


def parse_php(source_code: str) -> object:
    """
    Parse PHP source string and return tree-sitter tree.
    Handles both string and bytes input.
    """
    parser = get_parser()
    if isinstance(source_code, str):
        source_code = source_code.encode("utf-8")
    return parser.parse(source_code)