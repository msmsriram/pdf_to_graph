import * as echarts from "echarts";
import { zipSync } from "fflate";
import { specToOption } from "./chartOption.js";

const FORMATS = ["png", "svg", "jpeg"];

function fileStem(value, fallback) {
  return String(value || "").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
    .trim().replace(/[. ]+$/g, "").slice(0, 100) || fallback;
}

async function renderChart(spec, format, theme) {
  const width = 840;
  const height = 520;
  const svg = format === "svg";
  const instance = echarts.init(svg ? null : document.createElement("div"), null, {
    renderer: svg ? "svg" : "canvas", ssr: svg, width, height,
  });
  try {
    const backgroundColor = theme === "dark" ? "#0a0e17" : "#f5f7fc";
    instance.setOption({
      ...specToOption(spec, { theme, scale: width / 560, width, height }),
      animation: false,
      backgroundColor,
    });
    if (svg) return new TextEncoder().encode(instance.renderToSVGString());

    const canvas = instance.renderToCanvas({ pixelRatio: 2, backgroundColor });
    const mimeType = `image/${format}`;
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, mimeType, 0.95));
    if (blob?.type !== mimeType) throw new Error(`Could not encode ${format.toUpperCase()}.`);
    return new Uint8Array(await blob.arrayBuffer());
  } finally {
    instance.dispose();
  }
}

export async function buildChartArchive(charts, format, theme, onProgress) {
  if (!FORMATS.includes(format) || !charts.length) throw new Error("Choose an image format for the charts.");
  const files = {};
  for (const [index, chart] of charts.entries()) {
    onProgress?.(index + 1);
    // Give the browser time to paint progress before rendering the next chart.
    await new Promise((resolve) => setTimeout(resolve, 0));
    const spec = chart.versions[chart.current_version].spec;
    const order = String(index + 1).padStart(String(charts.length).length, "0");
    const title = fileStem(spec.title, "chart");
    const name = `${order}-page-${chart.page_number}-${title}.${format}`;
    try {
      files[name] = await renderChart(spec, format, theme);
    } catch (cause) {
      throw new Error(`Could not export “${title}” on page ${chart.page_number}.`, { cause });
    }
  }
  return new Blob([zipSync(files, { level: format === "svg" ? 6 : 0 })], { type: "application/zip" });
}

export async function downloadChartArchive(charts, format, theme, documentName, onProgress) {
  const blob = await buildChartArchive(charts, format, theme, onProgress);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${fileStem(documentName, "document")}-charts-${format}.zip`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
