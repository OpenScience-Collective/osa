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


def test_parse_help_after_definition():
    """MATLAB's own convention: help follows the definition (FieldTrip, Brainstorm)."""
    code = """function [data] = ft_preprocessing(cfg, data)

% FT_PREPROCESSING reads MEG and/or EEG data according to user-specified trials
% and applies several user-specified preprocessing steps to the signals.
%
% Use as
%   [data] = ft_preprocessing(cfg)

% Copyright (C) 2003-2013, Robert Oostenveld, SMI, FCDC

ft_defaults
"""
    results = parse_matlab_file(code, "ft_preprocessing.m")

    assert len(results) == 1
    assert results[0].symbol_name == "ft_preprocessing"
    # FieldTrip leaves a blank line after the definition; the block starts after it
    assert results[0].line_number == 3
    assert "FT_PREPROCESSING reads MEG" in results[0].docstring
    assert "Use as" in results[0].docstring
    # The block ends at the first blank line, before the copyright notice
    assert "Copyright" not in results[0].docstring


def test_parse_brainstorm_style_leaves_out_license():
    """Brainstorm's help block is followed by a license block, after a blank line."""
    code = """function varargout = process_bandpass( varargin )
% PROCESS_BANDPASS: Frequency filters: Lowpass/Highpass/Bandpass
%
% USAGE:  sProcess = process_bandpass('GetDescription')
%           sInput = process_bandpass('Run', sProcess, sInput)

% @=============================================================================
% This function is part of the Brainstorm software:
% https://neuroimage.usc.edu/brainstorm
% =============================================================================@
%
% Authors: Francois Tadel, 2010-2019

eval(macro_method);
end


%% ===== GET DESCRIPTION =====
function sProcess = GetDescription()
    % Description the process
    sProcess.Comment = 'Band-pass filter';
end
"""
    results = parse_matlab_file(code, "process_bandpass.m")
    by_name = {r.symbol_name: r for r in results}

    main = by_name["process_bandpass"]
    assert main.docstring.startswith("PROCESS_BANDPASS: Frequency filters")
    assert "USAGE:" in main.docstring
    assert "Brainstorm software" not in main.docstring
    assert "Authors" not in main.docstring
    # A subfunction keeps the banner before it rather than its first code comment
    assert by_name["GetDescription"].docstring == "===== GET DESCRIPTION ====="


def test_parse_help_after_continued_definition():
    """The help block starts after a definition split over lines with ``...``."""
    code = """function out = long_signature(a, ...
                                b)
% LONG_SIGNATURE does something
out = a + b;
"""
    results = parse_matlab_file(code, "long_signature.m")

    assert len(results) == 1
    assert results[0].docstring == "LONG_SIGNATURE does something"
    assert results[0].line_number == 3


def test_parse_help_before_definition_wins():
    """EEGLAB's header before the definition is kept over a comment after it."""
    code = """% POP_THING - the documented help

function out = pop_thing(x)
% internal note
out = x;
"""
    results = parse_matlab_file(code, "pop_thing.m")

    assert len(results) == 1
    assert results[0].docstring == "POP_THING - the documented help"


def test_parse_block_comment_license_is_not_help():
    """A %{ ... %} license block before the definition is skipped for the help after it."""
    code = """%{
    Copyright (C) Cambridge Electronic Design Limited 2014
    This file is part of CEDS64ML.
%}

function [ iOk ] = CEDS64Close( fhand )
%CEDS64CLOSE Closes an SON file
iOk = 0;
"""
    results = parse_matlab_file(code, "CEDS64Close.m")

    assert len(results) == 1
    assert results[0].symbol_name == "CEDS64Close"
    assert results[0].docstring == "CEDS64CLOSE Closes an SON file"


def test_parse_license_only_block_is_not_help():
    """A function whose only comment after its definition is a license has no help."""
    code = """function y = bst_nanmean(varargin)

% @=============================================================================
% This function is part of the Brainstorm software:
% =============================================================================@

y = 1;
"""
    assert parse_matlab_file(code, "bst_nanmean.m") == []


def test_parse_help_stops_at_license_without_blank_line():
    """A help block that runs into the license notice ends where the notice starts."""
    code = """function X = bst_epoching(X)
% BST_EPOCHING: Divides X in epochs
%
% @=============================================================================
% This function is part of the Brainstorm software:
X = X;
"""
    results = parse_matlab_file(code, "bst_epoching.m")

    assert len(results) == 1
    assert results[0].docstring == "BST_EPOCHING: Divides X in epochs"


def test_parse_classdef_header_is_the_file_help():
    """A classdef file's top comment block is its help, even when methods have their own."""
    code = """%CVPARTITION Create a cross-validation partition for data.
%   An object of the CVPARTITION class defines a random partition.

classdef cvpartition
  methods
    function obj = cvpartition(varargin)
      % constructor
    end
    function obj = repartition(obj)
      % REPARTITION Rerandomize a cross-validation partition.
    end
  end
end
"""
    results = parse_matlab_file(code, "cvpartition.m")
    by_name = {r.symbol_name: r for r in results}

    assert by_name["cvpartition"].symbol_type == "script"
    assert by_name["cvpartition"].docstring.startswith("CVPARTITION Create a cross-validation")
    assert "Rerandomize" in by_name["repartition"].docstring
    assert len(results) == 2


def test_parse_separator_above_definition_does_not_hide_help_below():
    """A bare "%%%%" separator above a function is no help; the block below is read."""
    code = """%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
function y = helper(x)
% HELPER doubles x
y = 2 * x;
"""
    results = parse_matlab_file(code, "helper.m")

    assert len(results) == 1
    assert results[0].docstring == "HELPER doubles x"


def test_parse_continuation_with_trailing_comment():
    """A "..." continuation followed by a comment still continues the definition."""
    code = """function out = f(a, ... % first input
                 b)
% F adds its inputs
out = a + b;
"""
    results = parse_matlab_file(code, "f.m")

    assert len(results) == 1
    assert results[0].docstring == "F adds its inputs"


def test_parse_script_header_stops_at_license():
    """A script's help ends where the license notice starts."""
    code = """% Batch import of the auditory dataset
%
% @=============================================================================
% This function is part of the Brainstorm software:

sFiles = [];
"""
    results = parse_matlab_file(code, "batch_import.m")

    assert len(results) == 1
    assert results[0].docstring == "Batch import of the auditory dataset"


def test_parse_classdef_with_license_only_header_keeps_constructor():
    """A classdef whose top block is only a license keeps its constructor's help."""
    code = """% Copyright (C) 2020 Someone

classdef Foo < handle
  methods
    function obj = Foo()
      % FOO builds a Foo
    end
  end
end
"""
    results = parse_matlab_file(code, "Foo.m")

    assert len(results) == 1
    assert results[0].symbol_type == "function"
    assert results[0].docstring == "FOO builds a Foo"


def test_parse_classdef_help_after_classdef_line():
    """MATLAB's convention also puts a class's help after its classdef line."""
    code = """classdef Bar < handle
% BAR holds a value
  properties
    value
  end
end
"""
    results = parse_matlab_file(code, "Bar.m")

    assert len(results) == 1
    assert results[0].symbol_name == "Bar"
    assert results[0].docstring == "BAR holds a value"
