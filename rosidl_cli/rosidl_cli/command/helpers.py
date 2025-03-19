# Copyright 2021 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import json
import os
import pathlib
import tempfile


def package_name_from_interface_file_path(path):
    """
    Derive ROS package name from a ROS interface definition file path.

    This function assumes ROS interface definition files follow the typical
    ``rosidl`` install space layout i.e. 'package_name/subfolder/interface.idl'.
    """
    return pathlib.Path(os.path.abspath(path)).parents[1].name


def dependencies_from_include_paths(include_paths):
    """
    Collect dependencies' ROS interface definition files from include paths.

    Interface definition file paths from dependencies are absolute paths
    prefixed by the name of package they belong to followed by a colon ':'.
    """
    return list({
        f'{package_name_from_interface_file_path(path)}:{path}'
        for include_path in include_paths
        for path in pathlib.Path(
            os.path.abspath(include_path)
        ).glob('**/*.idl')
    })


def interface_path_as_tuple(path):
    """
    Express interface definition file path as an (absolute prefix, relative path) tuple.

    An interface definition file path is a relative path, optionally prefixed
    by a path against which to resolve the former followed by a colon ':'.
    Thus, this function applies following logic:

    - If a given path follows this pattern, it is split at the colon ':'
    - If a given path is prefixed by a relative path, it is resolved
        relative to the current working directory.
    - If a given path has no prefix, the current working directory is
        used as prefix.
    """
    path_as_string = str(path)
    if ':' not in path_as_string:
        prefix = pathlib.Path.cwd()
    else:
        prefix, _, path = path_as_string.rpartition(':')
        prefix = pathlib.Path(os.path.abspath(prefix))
    path = pathlib.Path(path)
    if path.is_absolute():
        raise ValueError('Interface definition file path '
                         f"'{path}' cannot be absolute")
    return prefix, path


def idl_tuples_from_interface_files(interface_files):
    """
    Express ROS interface definition file paths as IDL tuples.

    An IDL tuple is a relative path prefixed by an absolute path against
    which to resolve it followed by a colon ':'. This function then applies
    the same logic as `interface_path_as_tuple`.
    """
    idl_tuples = []
    for path in interface_files:
        prefix, path = interface_path_as_tuple(path)
        idl_tuples.append(f'{prefix}:{path.as_posix()}')
    return idl_tuples


def build_type_description_tuples(idl_interface_files, type_description_files):
    """
    Create type description tuples from IDL interface files and type descriptions.

    :param idl_interface_files: List of IDL interface files either with or without prefix
    :param type_description_files: List of type description files
    :return: List of type description tuples of the form 'idl_file_path:type_description_file'
    """
    def get_type_description_file(idl_file, type_description_files):
        for type_description_file in type_description_files:
            if pathlib.Path(idl_file).stem == pathlib.Path(type_description_file).stem:
                return type_description_file

    type_description_tuples = []
    for idl_file in idl_interface_files:
        type_description_file = get_type_description_file(idl_file, type_description_files)
        assert type_description_file is not None, \
            f"Type description file not found for {idl_file}"
        _, path = interface_path_as_tuple(idl_file)
        type_description_tuples.append(f"{path}:{type_description_file}")
    return type_description_tuples

@contextlib.contextmanager
def legacy_generator_arguments_file(
    *,
    package_name,
    interface_files,
    include_paths,
    templates_path,
    output_path,
    extra_args=None
):
    """
    Generate a temporary rosidl generator arguments file.

    :param package_name: Name of the ROS package for which to generate code
    :param interface_files: Relative paths to ROS interface definition files,
      optionally prefixed by another absolute or relative path followed by
      a colon ':'. The former relative paths will be used as a prototype to
      namespace generated code (if applicable).
    :param include_paths: Paths where ROS package dependencies' interface
      definition files may be found
    :param templates_path: Path to the templates directory for the
      generator script this arguments are for
    :param output_path: Path to the output directory for generated code
    :param extra_args: Dict of additional arguments to be included in the generator
      arguments file, they'll be included as they are passed without any processing
    """

    arguments = {}
    arguments['package_name'] = package_name
    arguments['output_dir'] = os.path.abspath(output_path)
    arguments['template_dir'] = os.path.abspath(templates_path)
    arguments['idl_tuples'] = idl_tuples_from_interface_files(interface_files)
    arguments['ros_interface_dependencies'] = dependencies_from_include_paths(include_paths)
    # TODO(hidmic): re-enable output file caching
    arguments['target_dependencies'] = []
    # NOTE(frneer): Add additional arguments directly to the output file.
    # This allows extending the generator argument file externally
    arguments.update(extra_args or {})

    # NOTE(hidmic): named temporary files cannot be opened twice on Windows,
    # so close it and manually remove it when leaving the context
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
        tmp.write(json.dumps(arguments))
    path_to_file = os.path.abspath(tmp.name)
    try:
        yield path_to_file
    finally:
        try:
            os.unlink(path_to_file)
        except FileNotFoundError:
            pass


def generate_visibility_control_file(
    *,
    package_name,
    template_path,
    output_path
):
    """
    Generate a visibility control file from a template.

    :param package_name: Name of the ROS package for which
      to generate the file.
    :param template_path: Path to template visibility control file.
      May contain @PROJECT_NAME@ and @PROJECT_NAME_UPPER@ placeholders,
      to be substituted by the package name, accordingly.
    :param output_path: Path to visibility control file after interpolation.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(template_path, 'r') as fd:
        content = fd.read()

    content = content.replace('@PROJECT_NAME@', package_name)
    content = content.replace('@PROJECT_NAME_UPPER@', package_name.upper())

    with open(output_path, 'w') as fd:
        fd.write(content)

def split_interface_files(interface_files):
    """Split interface files into IDL and non-IDL files."""
    idl_interface_files = []
    non_idl_interface_files = []
    for path in interface_files:
        if not path.endswith('.idl'):
            non_idl_interface_files.append(path)
        else:
            idl_interface_files.append(path)
    return idl_interface_files, non_idl_interface_files
