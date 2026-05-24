(function () {
  function initMediaViewer(root) {
    const scope = root || document;
    const viewer = scope.querySelector("[data-media-viewer]");
    if (!viewer || viewer.dataset.bound === "true") {
      return;
    }
    viewer.dataset.bound = "true";

    const imageNode = viewer.querySelector("[data-media-viewer-image]");
    const videoNode = viewer.querySelector("[data-media-viewer-video]");
    const titleNode = viewer.querySelector("[data-media-viewer-title]");
    const metaNode = viewer.querySelector("[data-media-viewer-meta]");
    const downloadNode = viewer.querySelector("[data-media-viewer-download]");
    const closeNodes = viewer.querySelectorAll("[data-media-viewer-close]");
    const triggerNodes = scope.querySelectorAll("[data-media-viewer-trigger]");

    function closeViewer() {
      viewer.hidden = true;
      document.body.classList.remove("media-viewer-open");
      imageNode.hidden = true;
      imageNode.removeAttribute("src");
      videoNode.hidden = true;
      videoNode.pause();
      videoNode.removeAttribute("src");
      while (videoNode.firstChild) {
        videoNode.removeChild(videoNode.firstChild);
      }
      downloadNode.setAttribute("href", "#");
    }

    function openViewer(trigger) {
      const kind = trigger.dataset.mediaKind || "image";
      const src = trigger.dataset.mediaSrc || "";
      const download = trigger.dataset.mediaDownload || src;
      const title = trigger.dataset.mediaTitle || "";
      const meta = trigger.dataset.mediaMeta || "";

      titleNode.textContent = title;
      metaNode.textContent = meta;
      downloadNode.setAttribute("href", download);
      downloadNode.setAttribute("download", title || "media");

      if (kind === "video") {
        const sourceNode = document.createElement("source");
        sourceNode.src = src;
        videoNode.appendChild(sourceNode);
        videoNode.hidden = false;
        videoNode.load();
        imageNode.hidden = true;
        imageNode.removeAttribute("src");
      } else {
        imageNode.src = src;
        imageNode.hidden = false;
        videoNode.hidden = true;
        videoNode.pause();
        while (videoNode.firstChild) {
          videoNode.removeChild(videoNode.firstChild);
        }
      }

      viewer.hidden = false;
      document.body.classList.add("media-viewer-open");
    }

    triggerNodes.forEach((trigger) => {
      trigger.addEventListener("click", () => openViewer(trigger));
    });

    closeNodes.forEach((node) => {
      node.addEventListener("click", closeViewer);
    });

    document.addEventListener("keydown", (event) => {
      if (!viewer.hidden && event.key === "Escape") {
        closeViewer();
      }
    });
  }

  window.initMediaViewer = initMediaViewer;
})();
