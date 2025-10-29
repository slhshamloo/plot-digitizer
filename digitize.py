import re
import numpy as np
from argparse import ArgumentParser


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
        if len(transforms) > 0:
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


def _get_group_label(labels, svg_content, line_idx):
    i = line_idx + 1
    while i < len(svg_content) and not '<' in svg_content[i]:
        label_match = re.search(r'label="([^"]+)"', svg_content[i])
        if label_match:
            label = label_match.group(1)
            if label in labels:
                return label
        i += 1
    return None


def _get_path_data(transforms, svg_content, idx):
    path_label = None
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
        label_match = re.search(r'label="([^"]+)"', svg_content[i])
        if label_match:
            path_label = label_match.group(1)
        if '/>' in svg_content[i]:
            break
        i += 1
    return path_label, points, transformed


def _apply_transforms(points, transforms, path_transform):
    if len(transforms) == 0:
        return points
    homogeneous_points = np.vstack((points, np.ones(points.shape[1])))
    for transform in transforms[::-1]:
        homogeneous_points = transform @ homogeneous_points
    points = homogeneous_points[:2, :]
    if path_transform:
        transforms.pop()
    return points


def _extract_real_range(path_label):
    number_match = re.findall(r'[-+]?\d*\.?\d+e?[-+]?\d*', path_label)
    if len(number_match) != 2:
        raise RuntimeError(
            "Reference path label must contain exactly two numbers "
            f"indicating the real data range. Instead, got: '{path_label}'"
            f"which contains {len(number_match)} numbers.")
    return [float(number_match[0]), float(number_match[1])]


def _get_svg_data(file_path, labels, mode, xref, yref):
    xy_dict = {label: np.empty((2, 0), dtype=float) for label in labels}
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
            current_group = _get_group_label(labels, svg_content, i)
        if '<path' in line:
            path_label, points, path_transform = _get_path_data(
                transforms, svg_content, i)
            if points is None:
                if path_transform:
                    transforms.pop()
                continue
            if path_label is not None and path_label.startswith(xref):
                print(transforms)
                print('\n')
            points = _apply_transforms(points, transforms, path_transform)

            if path_label is not None and path_label.startswith(xref):
                ref_points[0] = [points[0, 0], points[0, -1]]
                ref_real_range[0] = _extract_real_range(path_label)
            elif path_label is not None and path_label.startswith(yref):
                ref_points[1] = [points[1, 0], points[1, -1]]
                ref_real_range[1] = _extract_real_range(path_label)
            elif mode == 'group' and current_group is not None:
                # To judge the representative point of the group, I take the
                # midpoint of the bounding box. This tends to be more robust
                # than the mean of the points, because curves mess with the
                # uniformity of the distribution of points.
                point = (np.max(points, axis=1, keepdims=True)
                         + np.min(points, axis=1, keepdims=True)) / 2
                xy_dict[current_group] = np.hstack(
                    (xy_dict[current_group], point))
            elif mode == 'path' and path_label in labels:
                xy_dict[path_label] = points
    return xy_dict, ref_points, ref_real_range


def digitize_svg(file_path, labels, mode='path',
                 xref='xref', yref='yref'):
    if mode not in ['path', 'group']:
        raise ValueError("Mode must be either 'path' or 'group'")
    xy_dict, ref_points, ref_real_range = _get_svg_data(
        file_path, labels, mode, xref, yref)
    if ref_points[0] is None:
        raise ValueError(f"Reference x path '{xref}' not found in SVG.")
    if ref_points[1] is None:
        raise ValueError(f"Reference y path '{yref}' not found in SVG.")
    ref_points = np.array(ref_points)
    ref_real_range = np.array(ref_real_range)
    for label in labels:
        xy_data = xy_dict[label]
        if xy_data.size == 0:
            continue
        # Normalize to ref points
        xy_data = ((xy_data - ref_points[:, 0][:, np.newaxis])
                   / (ref_points[:, 1] - ref_points[:, 0])[:, np.newaxis])
        # Scale to real data range
        xy_data = (xy_data * (ref_real_range[:,1]-ref_real_range[:,0]
                              )[:, np.newaxis]
                   + ref_real_range[:, 0][:, np.newaxis])
        xy_dict[label] = xy_data[:, np.argsort(xy_data[0, :])]
    return xy_dict


def digitize_svg_to_csv(file_path, labels, mode='path',
                        xref='xref', yref='yref', xheader='x', yheader='y'):
    xy_dict = digitize_svg(file_path, labels, mode, xref, yref)
    save_path_prefix = file_path[:-4] # remove .svg
    for label in labels:
        xy_data = xy_dict[label]
        if xy_data.size == 0:
            continue
        np.savetxt(f"{save_path_prefix}_{label}.csv", xy_data.T,
                   header=f"{xheader},{yheader}", delimiter=',')


def main():
    parser = ArgumentParser()
    parser.add_argument('filepath', help="Path to the SVG file")
    parser.add_argument('labels', nargs="+",
                        help="labels of the data series to extract")
    parser.add_argument(
        '-m', '--mode', choices=["path", "group"], default="path",
        help="Mode of operation. 'path' mode for lines and 'group'"
             " mode for scatter plots.")
    parser.add_argument('-x', '--xref', default='xref',
                        help="Label prefix for the reference x axis path")
    parser.add_argument('-y', '--yref', default='yref',
                        help="Label prefix for the reference y axis path")
    args = parser.parse_args()
    digitize_svg_to_csv(args.filepath, args.labels, mode=args.mode,
                        xref=args.xref, yref=args.yref)


if __name__ == "__main__":
    main()
