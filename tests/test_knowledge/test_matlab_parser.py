"""Tests for MATLAB docstring parser."""

from src.knowledge.matlab_parser import parse_matlab_file


def test_parse_function_with_docstring():
    """Test extraction of function with preceding comment block."""
    code = """% This is a test function
% It does something useful
%
% Usage:
%   result = test_func(input)
%
% Input:
%   input - some input data
%
% Output:
%   result - the processed result

function result = test_func(input)
    result = input * 2;
end
"""
    results = parse_matlab_file(code, "test_func.m")

    assert len(results) == 1
    assert results[0].symbol_name == "test_func"
    assert results[0].symbol_type == "function"
    assert "This is a test function" in results[0].docstring
    assert "Usage:" in results[0].docstring
    assert results[0].line_number < 13  # Comment starts before function


def test_parse_function_multiple_outputs():
    """Test function with multiple return values."""
    code = """% Calculate sum and product
% of two numbers

function [sum_result, prod_result] = calc(a, b)
    sum_result = a + b;
    prod_result = a * b;
end
"""
    results = parse_matlab_file(code, "calc.m")

    assert len(results) == 1
    assert results[0].symbol_name == "calc"
    assert results[0].symbol_type == "function"
    assert "Calculate sum and product" in results[0].docstring


def test_parse_function_no_outputs():
    """Test function with no return values."""
    code = """% Display a message
% to the console

function display_message(msg)
    disp(msg);
end
"""
    results = parse_matlab_file(code, "display_message.m")

    assert len(results) == 1
    assert results[0].symbol_name == "display_message"
    assert "Display a message" in results[0].docstring


def test_parse_script_with_header():
    """Test script (no function) with header comments."""
    code = """% Script to plot data
% This script loads data and creates visualizations
%
% Requirements:
%   - MATLAB R2020a or later
%   - Statistics Toolbox

data = load('data.mat');
plot(data.x, data.y);
title('My Plot');
"""
    results = parse_matlab_file(code, "plot_script.m")

    assert len(results) == 1
    assert results[0].symbol_name == "plot_script"
    assert results[0].symbol_type == "script"
    assert "Script to plot data" in results[0].docstring
    assert "Requirements:" in results[0].docstring


def test_parse_function_without_docstring():
    """Test function with no preceding comments."""
    code = """
function result = simple_func(x)
    result = x + 1;
end
"""
    results = parse_matlab_file(code, "simple_func.m")

    assert len(results) == 0


def test_parse_multiple_functions():
    """Test file with multiple functions."""
    code = """% Main function
% Does the main work

function output = main_func(input)
    output = helper_func(input);
end

% Helper function
% Assists the main function

function result = helper_func(data)
    result = data * 2;
end
"""
    results = parse_matlab_file(code, "multi_func.m")

    # Should find both functions
    assert len(results) == 2
    names = {r.symbol_name for r in results}
    assert "main_func" in names
    assert "helper_func" in names


def test_parse_comment_styles():
    """Test different MATLAB comment styles."""
    code = """%% This is a function
 % It has various comment styles
  %   Including indented comments
   % And multiple % characters

function result = test_comments(x)
    result = x;
end
"""
    results = parse_matlab_file(code, "test_comments.m")

    assert len(results) == 1
    # Docstring should have comment markers stripped
    assert "This is a function" in results[0].docstring
    assert "%" not in results[0].docstring.split("\n")[0]


def test_parse_empty_file():
    """Test empty file."""
    code = ""
    results = parse_matlab_file(code, "empty.m")

    assert len(results) == 0


def test_parse_comments_only_no_code():
    """Test file with only comments (script with no executable code)."""
    code = """% This is just documentation
% No actual code here
"""
    results = parse_matlab_file(code, "comments_only.m")

    # This should be treated as a script with header comments
    assert len(results) == 1
    assert results[0].symbol_type == "script"
    assert "This is just documentation" in results[0].docstring


def test_parse_function_with_blank_lines_in_comments():
    """Test function with blank lines in comment block."""
    code = """% Function to process data
%
% This function does something useful.
%
% Args:
%   input - the input data

function output = process_data(input)
    output = input;
end
"""
    results = parse_matlab_file(code, "process_data.m")

    assert len(results) == 1
    assert "Function to process data" in results[0].docstring
    # Blank lines should be preserved
    assert "\n\n" in results[0].docstring or results[0].docstring.count("\n") > 1


def test_parse_real_eeglab_style():
    """Test EEGLAB-style function documentation."""
    code = """% pop_loadset() - load an EEG dataset
%
% Usage:
%   >> EEGOUT = pop_loadset;
%   >> EEGOUT = pop_loadset( filename, filepath);
%
% Inputs:
%   filename - [string] dataset filename
%   filepath - [string] dataset filepath
%
% Outputs:
%   EEGOUT   - output dataset structure
%
% See also:
%   pop_saveset, eeg_checkset

function [EEG, com] = pop_loadset(filename, filepath)
    % function body
end
"""
    results = parse_matlab_file(code, "pop_loadset.m")

    assert len(results) == 1
    assert results[0].symbol_name == "pop_loadset"
    doc = results[0].docstring
    assert "pop_loadset()" in doc
    assert "Usage:" in doc
    assert "Inputs:" in doc
    assert "Outputs:" in doc
    assert "See also:" in doc


def test_parse_percent_sign_in_text():
    """Test handling of % within comment text."""
    code = """% Function to calculate 50% threshold
% The threshold is set at 50% of max value

function thresh = calc_threshold(data)
    thresh = max(data) * 0.5;
end
"""
    results = parse_matlab_file(code, "calc_threshold.m")

    assert len(results) == 1
    # The % in "50%" should be preserved in docstring
    assert "50%" in results[0].docstring
