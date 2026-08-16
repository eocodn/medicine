"use strict";

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function resizeWithin(width, height, maximumEdge) {
  const longest = Math.max(width, height);
  if (longest <= maximumEdge) return { width, height };
  const scale = maximumEdge / longest;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

function recognitionTargetWidth(
  cropWidth,
  cropHeight,
  recognitionHeight = 48,
  baseWidth = 320,
  maximumWidth = 1280,
) {
  if (![cropWidth, cropHeight, recognitionHeight, baseWidth, maximumWidth].every(Number.isFinite)
      || cropWidth <= 0 || cropHeight <= 0 || recognitionHeight <= 0
      || baseWidth <= 0 || maximumWidth < baseWidth) {
    throw new Error("invalid recognition dimensions");
  }
  const scaledWidth = Math.trunc(recognitionHeight * cropWidth / cropHeight);
  return Math.min(maximumWidth, Math.max(baseWidth, scaledWidth));
}

function rgbaToChw(rgba, width, height, mean, standardDeviation) {
  const pixels = width * height;
  const output = new Float32Array(3 * pixels);
  for (let index = 0; index < pixels; index += 1) {
    const source = index * 4;
    output[index] = (rgba[source + 2] / 255 - mean[0]) / standardDeviation[0];
    output[index + pixels] = (rgba[source + 1] / 255 - mean[1]) / standardDeviation[1];
    output[index + pixels * 2] = (rgba[source] / 255 - mean[2]) / standardDeviation[2];
  }
  return output;
}

function foregroundColumnInk(image) {
  const { data, width, height } = image;
  if (!data || !Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
      || data.length !== width * height * 4) throw new Error("invalid RGBA image data");
  const histogram = new Uint32Array(256);
  const gray = new Uint8Array(width * height);
  for (let index = 0; index < gray.length; index += 1) {
    const offset = index * 4;
    const value = Math.max(0, Math.min(255, Math.round(
      data[offset] * 0.299 + data[offset + 1] * 0.587 + data[offset + 2] * 0.114,
    )));
    gray[index] = value;
    histogram[value] += 1;
  }
  const total = gray.length;
  let weightedTotal = 0;
  for (let level = 0; level < 256; level += 1) weightedTotal += level * histogram[level];
  let backgroundWeight = 0;
  let backgroundSum = 0;
  let bestVariance = -1;
  let threshold = 0;
  for (let level = 0; level < 256; level += 1) {
    backgroundWeight += histogram[level];
    if (!backgroundWeight) continue;
    const foregroundWeight = total - backgroundWeight;
    if (!foregroundWeight) break;
    backgroundSum += level * histogram[level];
    const meanBackground = backgroundSum / backgroundWeight;
    const meanForeground = (weightedTotal - backgroundSum) / foregroundWeight;
    const variance = backgroundWeight * foregroundWeight * (meanBackground - meanForeground) ** 2;
    if (variance > bestVariance) {
      bestVariance = variance;
      threshold = level;
    }
  }
  const mask = new Uint8Array(total);
  for (let index = 0; index < total; index += 1) mask[index] = gray[index] <= threshold ? 1 : 0;

  // Match the 2x2 morphological opening used by the validated desktop crop refiner.
  const eroded = new Uint8Array(total);
  for (let y = 0; y < height - 1; y += 1) {
    for (let x = 0; x < width - 1; x += 1) {
      const i = y * width + x;
      if (mask[i] && mask[i + 1] && mask[i + width] && mask[i + width + 1]) eroded[i] = 1;
    }
  }
  const opened = new Uint8Array(total);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = y * width + x;
      if (!eroded[i]) continue;
      opened[i] = 1;
      if (x + 1 < width) opened[i + 1] = 1;
      if (y + 1 < height) opened[i + width] = 1;
      if (x + 1 < width && y + 1 < height) opened[i + width + 1] = 1;
    }
  }
  const columns = Array(width).fill(0);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) columns[x] += opened[y * width + x];
  }
  return columns;
}

function splitHorizontalInkRanges(columnInk, cropHeight) {
  if (!Number.isInteger(cropHeight) || cropHeight <= 0) throw new Error("cropHeight must be positive");
  const width = columnInk.length;
  if (width <= 1) return [[0, width]];
  const blankLimit = Math.max(1, Math.round(cropHeight * 0.02));
  const minimumGap = Math.max(1, Math.ceil(cropHeight * 0.5));
  const minimumContent = Math.max(1, Math.ceil(cropHeight * 0.25));
  const gaps = [];
  let start = null;
  for (let index = 0; index < width; index += 1) {
    const blank = columnInk[index] <= blankLimit;
    if (blank && start === null) start = index;
    else if (!blank && start !== null) {
      if (start > 0 && index < width && index - start >= minimumGap) gaps.push([start, index]);
      start = null;
    }
  }
  if (!gaps.length) return [[0, width]];
  const boundaries = [0, ...gaps.map(([left, right]) => Math.floor((left + right) / 2)), width];
  const ranges = [];
  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const left = boundaries[index];
    const right = boundaries[index + 1];
    let first = -1;
    let last = -1;
    for (let x = left; x < right; x += 1) {
      if (columnInk[x] > blankLimit) {
        if (first < 0) first = x;
        last = x;
      }
    }
    if (first >= 0 && last - first + 1 >= minimumContent) ranges.push([left, right]);
  }
  return ranges.length ? ranges : [[0, width]];
}

function horizontalSubpolygon(polygon, start, end, cropWidth) {
  if (!Array.isArray(polygon) || polygon.length !== 4 || cropWidth <= 0
      || start < 0 || end <= start || end > cropWidth) throw new Error("invalid horizontal crop range");
  const [topLeft, topRight, bottomRight, bottomLeft] = polygon;
  const interpolate = (left, right, fraction) => [
    left[0] + (right[0] - left[0]) * fraction,
    left[1] + (right[1] - left[1]) * fraction,
  ];
  const startFraction = start / cropWidth;
  const endFraction = end / cropWidth;
  return [
    interpolate(topLeft, topRight, startFraction),
    interpolate(topLeft, topRight, endFraction),
    interpolate(bottomLeft, bottomRight, endFraction),
    interpolate(bottomLeft, bottomRight, startFraction),
  ];
}

function cross(origin, a, b) {
  return (a[0] - origin[0]) * (b[1] - origin[1])
    - (a[1] - origin[1]) * (b[0] - origin[0]);
}

function convexHull(points) {
  const sorted = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (sorted.length <= 2) return sorted;
  const lower = [];
  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), point) <= 0) lower.pop();
    lower.push(point);
  }
  const upper = [];
  for (let index = sorted.length - 1; index >= 0; index -= 1) {
    const point = sorted[index];
    while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), point) <= 0) upper.pop();
    upper.push(point);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

function orderQuad(points) {
  const sums = points.map((point) => point[0] + point[1]);
  const differences = points.map((point) => point[0] - point[1]);
  return [
    points[sums.indexOf(Math.min(...sums))],
    points[differences.indexOf(Math.max(...differences))],
    points[sums.indexOf(Math.max(...sums))],
    points[differences.indexOf(Math.min(...differences))],
  ].map((point) => [...point]);
}

function minimumAreaBox(points) {
  const hull = convexHull(points);
  if (hull.length < 2) return null;
  let best = null;
  for (let index = 0; index < hull.length; index += 1) {
    const current = hull[index];
    const next = hull[(index + 1) % hull.length];
    const angle = Math.atan2(next[1] - current[1], next[0] - current[0]);
    const cosine = Math.cos(angle);
    const sine = Math.sin(angle);
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const point of hull) {
      const x = point[0] * cosine + point[1] * sine;
      const y = -point[0] * sine + point[1] * cosine;
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    }
    const area = (maxX - minX) * (maxY - minY);
    if (!best || area < best.area) best = { area, minX, minY, maxX, maxY, cosine, sine };
  }
  if (!best) return null;
  const rotated = [
    [best.minX, best.minY], [best.maxX, best.minY],
    [best.maxX, best.maxY], [best.minX, best.maxY],
  ];
  return orderQuad(rotated.map(([x, y]) => [
    x * best.cosine - y * best.sine,
    x * best.sine + y * best.cosine,
  ]));
}

function distance(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

function minimumSide(box) {
  return Math.min(distance(box[0], box[1]), distance(box[1], box[2]));
}

function pointInside(pointX, pointY, polygon) {
  let inside = false;
  for (let current = 0, previous = polygon.length - 1; current < polygon.length; previous = current++) {
    const [currentX, currentY] = polygon[current];
    const [previousX, previousY] = polygon[previous];
    if (((currentY > pointY) !== (previousY > pointY))
      && pointX < ((previousX - currentX) * (pointY - currentY))
        / (previousY - currentY || Number.EPSILON) + currentX) inside = !inside;
  }
  return inside;
}

function boxScore(probabilities, width, height, box) {
  const minX = clamp(Math.floor(Math.min(...box.map((point) => point[0]))), 0, width - 1);
  const maxX = clamp(Math.ceil(Math.max(...box.map((point) => point[0]))), 0, width - 1);
  const minY = clamp(Math.floor(Math.min(...box.map((point) => point[1]))), 0, height - 1);
  const maxY = clamp(Math.ceil(Math.max(...box.map((point) => point[1]))), 0, height - 1);
  let sum = 0;
  let count = 0;
  for (let y = minY; y <= maxY; y += 1) {
    for (let x = minX; x <= maxX; x += 1) {
      if (!pointInside(x + 0.5, y + 0.5, box)) continue;
      sum += probabilities[y * width + x];
      count += 1;
    }
  }
  return count ? sum / count : 0;
}

function expandBox(box, ratio) {
  const width = distance(box[0], box[1]);
  const height = distance(box[1], box[2]);
  if (width <= 0 || height <= 0) return null;
  const offset = (width * height * ratio) / (2 * (width + height));
  const center = box.reduce((sum, point) => [sum[0] + point[0] / 4, sum[1] + point[1] / 4], [0, 0]);
  const horizontal = [(box[1][0] - box[0][0]) / width, (box[1][1] - box[0][1]) / width];
  const vertical = [(box[3][0] - box[0][0]) / height, (box[3][1] - box[0][1]) / height];
  const halfWidth = width / 2 + offset;
  const halfHeight = height / 2 + offset;
  return [
    [-halfWidth, -halfHeight], [halfWidth, -halfHeight],
    [halfWidth, halfHeight], [-halfWidth, halfHeight],
  ].map(([x, y]) => [
    center[0] + horizontal[0] * x + vertical[0] * y,
    center[1] + horizontal[1] * x + vertical[1] * y,
  ]);
}

function componentPoints(probabilities, width, height, threshold) {
  const visited = new Uint8Array(width * height);
  const queue = new Int32Array(width * height);
  const components = [];
  for (let start = 0; start < probabilities.length; start += 1) {
    if (visited[start] || probabilities[start] <= threshold) continue;
    let head = 0;
    let tail = 1;
    queue[0] = start;
    visited[start] = 1;
    const points = [];
    while (head < tail) {
      const offset = queue[head++];
      const x = offset % width;
      const y = Math.floor(offset / width);
      let boundary = false;
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dx = -1; dx <= 1; dx += 1) {
          if (dx === 0 && dy === 0) continue;
          const nextX = x + dx;
          const nextY = y + dy;
          if (nextX < 0 || nextY < 0 || nextX >= width || nextY >= height) {
            boundary = true;
            continue;
          }
          const next = nextY * width + nextX;
          if (probabilities[next] <= threshold) boundary = true;
          else if (!visited[next]) {
            visited[next] = 1;
            queue[tail++] = next;
          }
        }
      }
      if (boundary) points.push([x, y]);
    }
    if (points.length >= 4) components.push(points);
  }
  return components;
}

function sortReadingOrder(boxes) {
  const sorted = boxes.slice().sort((a, b) => a.poly[0][1] - b.poly[0][1]
    || a.poly[0][0] - b.poly[0][0]);
  for (let index = 0; index < sorted.length - 1; index += 1) {
    for (let cursor = index; cursor >= 0; cursor -= 1) {
      if (Math.abs(sorted[cursor + 1].poly[0][1] - sorted[cursor].poly[0][1]) < 10
        && sorted[cursor + 1].poly[0][0] < sorted[cursor].poly[0][0]) {
        [sorted[cursor], sorted[cursor + 1]] = [sorted[cursor + 1], sorted[cursor]];
      } else break;
    }
  }
  return sorted;
}

function decodeDetectionMap(probabilities, width, height, sourceWidth, sourceHeight, options) {
  const boxes = [];
  const components = componentPoints(probabilities, width, height, options.threshold).slice(0, 1000);
  for (const points of components) {
    const box = minimumAreaBox(points);
    if (!box || minimumSide(box) < 3) continue;
    const score = boxScore(probabilities, width, height, box);
    if (score < options.boxThreshold) continue;
    const expanded = expandBox(box, options.unclipRatio);
    if (!expanded || minimumSide(expanded) < 5) continue;
    const poly = expanded.map((point) => [
      clamp(Math.round(point[0] * sourceWidth / width), 0, sourceWidth),
      clamp(Math.round(point[1] * sourceHeight / height), 0, sourceHeight),
    ]);
    boxes.push({ poly, score });
  }
  return sortReadingOrder(boxes);
}

function decodeCtc(data, dimensions, dictionary, sampleIndex = 0) {
  const [, timeSteps, classes] = dimensions;
  const offset = sampleIndex * timeSteps * classes;
  let previous = -1;
  let text = "";
  const probabilities = [];
  for (let step = 0; step < timeSteps; step += 1) {
    let selected = 0;
    let maximum = -Infinity;
    for (let index = 0; index < classes; index += 1) {
      const value = data[offset + step * classes + index];
      if (value > maximum) {
        maximum = value;
        selected = index;
      }
    }
    if (selected > 0 && selected !== previous && dictionary[selected - 1] !== undefined) {
      text += dictionary[selected - 1];
      probabilities.push(maximum);
    }
    previous = selected;
  }
  return {
    text: text.normalize("NFC"),
    score: probabilities.length
      ? probabilities.reduce((sum, value) => sum + value, 0) / probabilities.length : 0,
  };
}

module.exports = {
  decodeCtc,
  decodeDetectionMap,
  distance,
  foregroundColumnInk,
  horizontalSubpolygon,
  recognitionTargetWidth,
  resizeWithin,
  rgbaToChw,
  splitHorizontalInkRanges,
  sortReadingOrder,
};
