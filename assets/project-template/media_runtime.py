"""Explicit local diagnostic mode for the PyAV-bundled FFmpeg CPU dispatch."""
from pathlib import Path
import ctypes
import os
_library=None

def configure_cpu_mode(av, scalar=False):
    if not scalar:
        return 'automatic'
    if os.name!='nt':
        raise RuntimeError('--scalar-cpu 此实现仅适用于本机Windows PyAV wheel；其他平台使用独立媒体后端')
    libraries=list((Path(av.__file__).resolve().parent.parent/'av.libs').glob('avutil-*.dll'))
    if len(libraries)!=1:
        raise RuntimeError('无法唯一定位当前PyAV自带的avutil DLL，未修改CPU模式')
    global _library
    _library=ctypes.CDLL(str(libraries[0]))
    _library.av_force_cpu_flags.argtypes=[ctypes.c_int]
    _library.av_force_cpu_flags.restype=None
    _library.av_get_cpu_flags.argtypes=[]
    _library.av_get_cpu_flags.restype=ctypes.c_int
    _library.av_force_cpu_flags(0)
    if _library.av_get_cpu_flags()!=0:
        raise RuntimeError('FFmpeg scalar CPU模式未生效')
    return 'scalar(cpu_flags=0)'
