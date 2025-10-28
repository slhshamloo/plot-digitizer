import re
import numpy as np


def _get_rotate_matrix(angle, cx=0, cy=0):
    angle_rad = np.radians(angle)
    cos_a = np.cos(angle_rad)
    sin_a = np.sin(angle_rad)
    translate_to_origin = np.array([[1, 0, -cx],
                                    [0, 1, -cy],
                                    [0, 0, 1]])
    rotation_matrix = np.array([[cos_a, -sin_a, 0],
                                [sin_a, cos_a, 0],
                                [0, 0, 1]])
    translate_back = np.array([[1, 0, cx],
                               [0, 1, cy],
                               [0, 0, 1]])
    return translate_back @ rotation_matrix @ translate_to_origin


def _get_transform(line):
    matrix = None
    transform_match = re.search(r'transform="([^"]+)"', line)
    if transform_match:
        transform_str = transform_match.group(1)
        values = re.findall(r'[-+]?\d*\.?\d+e?[-+]?\d*', transform_str)
        if 'matrix' in transform_str:
            matrix = np.array(values, dtype=float).reshape(3, 2).T
            matrix = np.vstack([matrix, [0, 0, 1]])
        if 'translate' in transform_str:
            tx = float(values[0])
            if len(values) == 2:
                ty = float(values[1])
            else:
                ty = 0.0
            matrix = np.array([[1, 0, tx],
                               [0, 1, ty],
                               [0, 0, 1]])
        if 'scale' in transform_str:
            if len(values) == 1:
                sx = sy = float(values[0])
            else:
                sx, sy = float(values[0]), float(values[1])
            matrix = np.array([[sx, 0, 0],
                               [0, sy, 0],
                               [0, 0, 1]])
        if 'rotate' in transform_str:
            angle = float(values[0])
            if len(values) == 3:
                cx, cy = float(values[1]), float(values[2])
            else:
                cx, cy = 0, 0
            matrix = _get_rotate_matrix(angle, cx, cy)
    return matrix


def _get_path_points(line):
    d_match = re.search(r'(?<!\S)d="([^"]+)"', line)
    if not d_match:
        return None

    d_str = d_match.group(1)
    commands = re.findall(r'([MmLlHhVvAaCcQqSsTt])|([-+]?\d*\.?\d+e?[-+]?\d*)', d_str)
    points = np.empty((0, 2), dtype=float)
    current_pos = np.array([0.0, 0.0])
    i = 0
    command = 'm'
    while i < len(commands):
        if commands[i][0]:
            command = commands[i][0]
            i += 1
        if command in 'MmLlAaCcSsTtQq':
            # skip control points
            if command in 'Aa':
                i += 5
            if command in 'Cc':
                i += 4
            if command in 'SsQq':
                i += 2
            x = float(commands[i][1])
            i += 1
            y = float(commands[i][1])
            if command.islower():
                current_pos += np.array([x, y])
            else:
                current_pos = np.array([x, y])
        elif command == 'H':
            current_pos[0] = float(commands[i][1])
        elif command == 'h':
            current_pos[0] += float(commands[i][1])
        elif command == 'V':
            current_pos[1] = float(commands[i][1])
        elif command == 'v':
            current_pos[1] += float(commands[i][1])
        points = np.vstack((points, current_pos))
        i += 1
    if len(points) == 1:
        print(commands)
    return np.array(points).T


def _handle_group_transform(transforms, svg_content, line_idx):
    line = svg_content[line_idx]
    if '</g>' in line:
        if transforms:
            transforms.pop()
    if re.search(r'<g(?!\S)', line): # match <g and not <g...
        # find the transformation
        i = line_idx + 1
        found = False
        while i < len(svg_content) and not '<' in svg_content[i]:
            transform = _get_transform(svg_content[i])
            if transform is not None:
                transforms.append(transform)
                found = True
                break
            i += 1
        if not found:
            transforms.append(np.eye(3))


def _get_group_name(names, svg_content, line_idx):
    i = line_idx + 1
    while i < len(svg_content) and not '<' in svg_content[i]:
        name_match = re.search(r'label="([^"]+)"', svg_content[i])
        if name_match:
            name = name_match.group(1)
            if name in names:
                return name
        i += 1
    return None


def _get_path_data(transforms, svg_content, idx):
    path_name = None
    points = None
    transformed = False
    i = idx + 1
    while i < len(svg_content):
        xy = _get_path_points(svg_content[i])
        if xy is not None:
            points = xy
        transform = _get_transform(svg_content[i])
        if transform is not None:
            transforms.append(transform)
            transformed = True
        name_match = re.search(r'label="([^"]+)"', svg_content[i])
        if name_match:
            path_name = name_match.group(1)
        if '/>' in svg_content[i]:
            break
        i += 1
    return path_name, points, transformed


def _apply_transforms(points, transforms, path_transform):
    homogeneous_points = np.vstack((points, np.ones(points.shape[1])))
    homogeneous_points = transforms[-1] @ homogeneous_points
    points = homogeneous_points[:2, :]
    if path_transform:
        transforms.pop()
    return points


def _extract_real_range(path_name):
    number_match = re.findall(r'[-+]?\d*\.?\d+e?[-+]?\d*', path_name)
    if len(number_match) != 2:
        raise RuntimeError(
            "Reference path name must contain exactly two numbers "
            f"indicating the real data range. Instead, got: '{path_name}'"
            f"which contains {len(number_match)} numbers.")
    return [float(number_match[0]), float(number_match[1])]


def _get_svg_data(file_path, names, mode, xref, yref):
    xy_dict = {name: np.empty((2, 0), dtype=float) for name in names}
    ref_points = [None, None]
    ref_real_range = [None, None]
    transforms = []
    if mode == 'group':
        current_group = None

    with open(file_path, 'r') as svg_file:
        svg_content = svg_file.readlines()
    for i, line in enumerate(svg_content):
        line = svg_content[i]
        _handle_group_transform(transforms, svg_content, i)
        if mode == 'group' and '</g>' in line:
            current_group = None
        if mode == 'group' and re.search(r'<g(?!\S)', line):
            current_group = _get_group_name(names, svg_content, i)
        if '<path' in line:
            path_name, points, path_transform = _get_path_data(
                transforms, svg_content, i)
            if points is None:
                continue
            points = _apply_transforms(points, transforms, path_transform)

            if path_name is not None and path_name.startswith(xref):
                ref_points[0] = [points[0, 0], points[0, -1]]
                ref_real_range[0] = _extract_real_range(path_name)
            elif path_name is not None and path_name.startswith(yref):
                ref_points[1] = [points[1, 0], points[1, -1]]
                ref_real_range[1] = _extract_real_range(path_name)
            elif mode == 'group' and current_group is not None:
                # To judge the representative point of the group, I take the
                # midpoint of the bounding box. This tends to be more robust
                # than the mean of the points, because curves mess with the
                # uniformity of the distribution of points.
                point = (np.max(points, axis=1, keepdims=True)
                         + np.min(points, axis=1, keepdims=True)) / 2
                xy_dict[current_group] = np.hstack(
                    (xy_dict[current_group], point))
            elif mode == 'path' and path_name in names:
                xy_dict[path_name] = points
    return xy_dict, ref_points, ref_real_range


def digitize_svg(file_path, names, mode='path',
                 xref='xref', yref='yref'):
    if mode not in ['path', 'group']:
        raise ValueError("Mode must be either 'path' or 'group'")
    xy_dict, ref_points, ref_real_range = _get_svg_data(
        file_path, names, mode, xref, yref)
    if ref_points[0] is None:
        raise ValueError(f"Reference x path '{xref}' not found in SVG.")
    if ref_points[1] is None:
        raise ValueError(f"Reference y path '{yref}' not found in SVG.")
    ref_points = np.array(ref_points)
    ref_real_range = np.array(ref_real_range)
    for name in names:
        xy_data = xy_dict[name]
        if xy_data.size == 0:
            continue
        # Normalize to ref points
        xy_data = ((xy_data - ref_points[:, 0][:, np.newaxis])
                   / (ref_points[:, 1] - ref_points[:, 0])[:, np.newaxis])
        # Scale to real data range
        xy_data = (xy_data * (ref_real_range[:,1]- ref_real_range[:,0]
                              )[:, np.newaxis]
                   + ref_real_range[:, 0][:, np.newaxis])
        xy_dict[name] = xy_data[:, np.argsort(xy_data[0, :])]
    return xy_dict


def digitize_svg_to_csv(file_path, names, mode='path',
                        xref='xref', yref='yref', xheader='x', yheader='y'):
    xy_dict = digitize_svg(file_path, names, mode, xref, yref)
    save_path_prefix = file_path[:-4] # remove .svg
    for name in names:
        xy_data = xy_dict[name]
        if xy_data.size == 0:
            continue
        np.savetxt(f"{save_path_prefix}_{name}.csv", xy_data.T,
                   header=f"{xheader},{yheader}", delimiter=',')


def main():
    digitize_svg_to_csv("OD13K.svg", names=["OD13K"], mode="group")


if __name__ == "__main__":
    main()
