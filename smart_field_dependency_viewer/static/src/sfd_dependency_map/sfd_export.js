import { downloadFile } from "@web/core/network/download";

const IMAGE_SCALE = 2;

/**
 * The file name of an export: <model>-<field>-dependencies.<extension>.
 * Requirements: FR-SFD-0011, FR-SFD-0012
 */
export function exportFileName(map, extension) {
    return `${map.start.model}-${map.start.field}-dependencies.${extension}`;
}

/**
 * The data file text: the map as the screen shows it, without the drawing.
 * Requirements: FR-SFD-0012
 */
export function dataFileContent(map) {
    const { svg, ...data } = map;
    return JSON.stringify(data, null, 2);
}

/**
 * Download the map as a JSON data file.
 * Requirements: FR-SFD-0012
 */
export function exportData(map) {
    downloadFile(dataFileContent(map), exportFileName(map, "json"), "application/json");
}

function loadImage(url) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = reject;
        image.src = url;
    });
}

/**
 * Draw the diagram's SVG on a white canvas and return it as a PNG blob.
 * Requirements: FR-SFD-0011
 */
export async function svgToPngBlob(svg) {
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    try {
        const image = await loadImage(url);
        const canvas = document.createElement("canvas");
        canvas.width = image.naturalWidth * IMAGE_SCALE;
        canvas.height = image.naturalHeight * IMAGE_SCALE;
        const context = canvas.getContext("2d");
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        return await new Promise((resolve, reject) =>
            canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("no image"))), "image/png")
        );
    } finally {
        URL.revokeObjectURL(url);
    }
}

/**
 * Download the diagram as a PNG image, marks included (they are in the SVG).
 * Requirements: FR-SFD-0011
 */
export async function exportImage(map) {
    const blob = await svgToPngBlob(map.svg);
    downloadFile(blob, exportFileName(map, "png"), "image/png");
}
