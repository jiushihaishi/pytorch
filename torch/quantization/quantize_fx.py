from .fx import Fuser  # noqa: F401
from .fx import Quantizer  # noqa: F401
from torch.fx import GraphModule  # type: ignore
from .fx.utils import graph_pretty_str  # noqa: F401

def _check_is_graph_module(model):
    if not isinstance(model, GraphModule):
        raise ValueError(
            'input model must be a GraphModule, ' +
            'please run torch.fx.symbolic_trace on your model before using ' +
            'quantize_fx. Got type:' + str(type(model)))

def fuse_fx(graph_module, inplace=False):
    r""" Fuse modules in preparation for quantization

    Args:
        graph_module: GraphModule object from symbolic tracing (torch.fx.symbolic_trace)
    """
    _check_is_graph_module(graph_module)
    fuser = Fuser()
    return fuser.fuse(graph_module, inplace)

def _prepare_fx(graph_module, qconfig_dict, inplace, is_dynamic_quant, is_standalone_module=False):
    r""" Internal helper function for prepare_fx
    Args:
      graph_module, qconfig_dict, inplace: see docs for :func:`~torch.quantization.prepare_fx`
      `is_standalone_module`: a boolean flag indicates whether we are
      quantizing a standalone module or not, a standalone module
      is a submodule of the parent module that is not inlined in the
forward graph of the parent module,
      the way we quantize standalone module is described in:
      :func:`~torch.quantization._prepare_standalone_module_fx`
    """
    _check_is_graph_module(graph_module)
    graph_module = fuse_fx(graph_module, inplace)
    quantizer = Quantizer()
    prepare = quantizer.prepare_dynamic if is_dynamic_quant else quantizer.prepare
    return prepare(graph_module, qconfig_dict, inplace=True, is_standalone_module=is_standalone_module)

def _prepare_standalone_module_fx(graph_module, qconfig_dict, inplace=False):
    r""" [Internal use only] Prepare a standalone module, so that it can be used when quantizing the
    parent module.
    standalone_module means it a submodule that is not inlined in parent module,
        and will be quantized separately as one unit.

    input of the module is quantized in parent module, output of the module
    is quantized in the standalone module.
    Extra attributes in output GraphModule while preparing a standalone module:
        _standalone_module_observed_input_idxs(List[Int]): a list of indexs for the graph inputs that
                                         needs to be observed in parent module
        _output_is_observed(Bool): a boolean variable indicate whether the output of the
                                   custom module is observed or not

    """
    return _prepare_fx(graph_module, qconfig_dict, inplace, is_dynamic_quant=False, is_standalone_module=True)

def _prepare_dynamic_standalone_module_fx(graph_module, qconfig_dict, inplace=False):
    r""" See :func:`~torch.quantization.prepare_standalone_module_fx`
    """
    return _prepare_fx(graph_module, qconfig_dict, inplace, is_dynamic_quant=True, is_standalone_module=True)

def prepare_fx(graph_module, qconfig_dict, inplace=False):
    r""" Prepare a model for post training static quantization or
    qantization aware training, not for public use.

    Args:
      graph_module: model from symbolic_tracing (torch.fx.symbolic_trace), must be
      an eval model
      qconfig_dict: see :func:`~torch.quantization.quantize_fx`

    Return:
      A GraphModule with observer or fake quant modules, ready for
      calibration or quantization aware training
    """
    prepared = _prepare_fx(graph_module, qconfig_dict, inplace, is_dynamic_quant=False)
    return prepared

def prepare_static_fx(graph_module, qconfig_dict, inplace=False):
    assert not graph_module.training, 'prepare_static_fx only works for models in ' + \
        'eval mode'
    return prepare_fx(graph_module, qconfig_dict, inplace)

def prepare_qat_fx(graph_module, qconfig_dict, inplace=False):
    r""" Prepare a model for quantization aware training
    Args:
      graph_module: model from symbolic_tracing (torch.fx.symbolic_trace), must be
      a train model
      qconfig_dict: see :func:`~torch.quantization.quantize_fx`

    Return:
      A GraphModule with observer or fake quant modules, ready for
      calibration or quantization aware training
    """
    assert graph_module.training, 'prepare_qat_fx only works for models in ' + \
        'train mode'
    return prepare_fx(graph_module, qconfig_dict, inplace)

def prepare_dynamic_fx(graph_module, qconfig_dict, inplace=False):
    r""" Prepare a model for post training dynamic quantization
    """
    prepared = _prepare_fx(graph_module, qconfig_dict, inplace, True)
    return prepared

def _convert_fx(graph_module, inplace, debug, is_dynamic_quant, is_standalone_module=False):
    _check_is_graph_module(graph_module)
    quantizer = Quantizer()
    return quantizer.convert(graph_module, inplace, debug, is_dynamic_quant, is_standalone_module)

def convert_fx(graph_module, inplace=False, debug=False):
    r""" Convert a calibrated or trained model to a quantized model
    """
    return _convert_fx(graph_module, inplace, debug, is_dynamic_quant=False)

convert_static_fx = convert_fx
convert_qat_fx = convert_fx

def convert_dynamic_fx(graph_module, inplace=False, debug=False):
    return _convert_fx(graph_module, inplace, debug, is_dynamic_quant=True)

def _convert_standalone_module_fx(graph_module, inplace=False, debug=False):
    r""" [Internal use only] Convert a model produced by :func:`~torch.quantization.prepare_standalone_module_fx`
    and convert it to a quantized model

    The inputs will be quantized by parent module, checks `_standalone_module_observed_input_idxs` of
    input model and will treat these inputs as quantized
    also will not dequantize the final output
    Return:
      A quantized standalone module which accepts quantized input(if needed)
      and produces quantized output (if needed).
    """
    return _convert_fx(graph_module, inplace, debug, is_dynamic_quant=False, is_standalone_module=True)

def _convert_dynamic_standalone_module_fx(graph_module, inplace=False, debug=False):
    r""" See :func:`~torch.quantization.convert_standalone_module_fx`
    """
    return _convert_fx(graph_module, inplace, debug, is_dynamic_quant=True, is_standalone_module=True)

def _quantize_fx(model, qconfig_dict, run_fn=None, run_args=None, inplace=False,
                 debug=False, is_dynamic_quant=False):
    assert not model.training, 'quantize_fx is only used for post training ' + \
        'quantization(eval mode), for quantization aware training please use ' + \
        'prepare_qat_fx and convert_qat_fx.'

    if is_dynamic_quant:
        model = prepare_dynamic_fx(model, qconfig_dict, inplace)
        # inplace is True since the inplace option is already applied in previous step
        model = convert_dynamic_fx(model, inplace=True, debug=debug)
    else:
        assert run_fn, "Must provide calibration function for post training static quantization"
        assert run_args, "Must provide calibration dataset for post training static quantization"
        model = prepare_fx(model, qconfig_dict, inplace)
        run_fn(model, *run_args)
        # inplace is True since the inplace option is already applied in previous step
        model = convert_fx(model, inplace=True, debug=debug)

    return model


def quantize_static_fx(model, qconfig_dict, run_fn, run_args, inplace=False, debug=False):
    r"""Quantize the input float symbolically traced GraphModule model with
    post training static quantization

    First it will prepare the model for calibration, then it calls
    `run_fn` which will run the calibration step, after that we will
    convert the model to a quantized model.

    Args:
        `model`: input float TorchScript model
        `qconfig_dict`: qconfig_dict is a dictionary with the following configurations:
        qconfig_dict = {
        # optional, global config
        "": qconfig?,

        # optional, used for module and function types
        # could also be split into module_types and function_types if we prefer
        "object_type": [
          (torch.nn.Conv2d, qconfig?),
          (torch.nn.functional.add, qconfig?),
          ...,
         ],

        # optional, used for module names
        "module_name": [
          ("foo.bar", qconfig?)
          ...,
        ],

        # optional, matched in order, first match takes precedence
        "module_name_regex": [
          ("foo.*bar.*conv[0-9]+", qconfig?)
          ...,
        ],
        # priority (in increasing order): global, object_type, module_name_regex, module_name
        # qconfig == None means fusion and quantization should be skipped for anything
        # matching the rule

        # optional: specify the path for standalone modules
        # These modules are symbolically traced and quantized as one unit
        # User should also skip symbolic tracing through these modules
        # so that the call to the submodule appears as one call_module
        # node in the forward graph of the GraphModule
        "standalone_module_name": [
           "submodule.standalone"
        ]
        }
        `run_fn`: a calibration function for calibrating the prepared model
        `run_args`: positional arguments for `run_fn`
        `inplace`: carry out model transformations in-place, the original module is
        mutated
        `debug`: flag for producing a debug friendly model (preserve weight attribute)

    Return:
        Quantized TorchSciprt model.

    Example:
    ```python
    import torch
    from torch.quantization import get_default_qconfig
    from torch.quantization import quantize_fx

    graph_module = torch.fx.symbolic_trace(float_model.eval())
    qconfig = get_default_qconfig('fbgemm')
    def calibrate(model, data_loader):
        model.eval()
        with torch.no_grad():
            for image, target in data_loader:
                model(image)

    quantized_model = quantize_fx(
        graph_module,
        {'': qconfig},
        calibrate,
        [data_loader_test])
    ```
    """
    return _quantize_fx(
        model, qconfig_dict, run_fn, run_args, inplace, debug, is_dynamic_quant=False)

def quantize_dynamic_fx(model, qconfig_dict, inplace=False, debug=False):
    r"""Quantize the input float symbolically traced GraphModule model with
    post training dynamic quantization.
    Currently only qint8 quantization of torch.nn.Linear is supported.

    Args:
        `model`: input float TorchScript model
        `qconfig_dict`: qconfig_dict is a dictionary with names of sub modules as key and
        qconfig for that module as value, please see detailed
        descriptions in :func:`~torch.quantization.quantize_fx`
        `inplace`: carry out model transformations in-place, the original module is
        mutated
        `debug`: flag for producing a debug friendly model (preserve weight attribute)

    Return:
        Quantized TorchSciprt model.

    Example:
    ```python
    import torch
    from torch.quantization import per_channel_dynamic_qconfig
    from torch.quantization import quantize_dynmiac_fx

    graph_module = torch.fx.symbolic_trace(float_model.eval())
    qconfig = get_default_qconfig('fbgemm')
    def calibrate(model, data_loader):
        model.eval()
        with torch.no_grad():
            for image, target in data_loader:
                model(image)

    quantized_model = quantize_dynamic_fx(
        graph_module,
        {'': qconfig},
        calibrate,
        [data_loader_test])
    ```
    """
    return _quantize_fx(
        model, qconfig_dict, inplace=inplace, debug=debug, is_dynamic_quant=True)
