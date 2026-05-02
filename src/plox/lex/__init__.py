"""Lexer construction pipeline: regex source -> NFA -> DFA -> minimised DFA.

Stages live in separate modules so each can be unit-tested without depending
on the others.
"""
