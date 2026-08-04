import { themes as prismThemes } from "prism-react-renderer";

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: "GrowCam PC",
  tagline: "A local desktop dashboard for the VIVOSUN GrowCam C4",
  favicon: "img/growcam-mark.svg",
  url: "https://nick2bad4u.github.io",
  baseUrl: "/growcam-pc/",
  organizationName: "Nick2bad4u",
  projectName: "growcam-pc",
  trailingSlash: false,
  onBrokenLinks: "throw",
  i18n: {
    defaultLocale: "en",
    locales: ["en"],
  },
  presets: [
    [
      "classic",
      {
        docs: {
          sidebarPath: "./sidebars.mjs",
          editUrl: "https://github.com/Nick2bad4u/growcam-pc/edit/main/website/",
          showLastUpdateTime: true,
        },
        blog: false,
        sitemap: {
          changefreq: "weekly",
          priority: 0.7,
        },
        theme: {
          customCss: "./src/css/custom.css",
        },
      },
    ],
  ],
  themeConfig: {
    metadata: [
      { name: "theme-color", content: "#071418" },
      {
        name: "description",
        content: "Open-source VIVOSUN GrowCam C4 desktop viewer with local live video, 24-hour rewind, time-lapse previews, and camera file downloads.",
      },
      {
        name: "keywords",
        content: "VIVOSUN GrowCam C4, GrowCam C4 PC viewer, GrowCam time-lapse, GrowCam rewind, RTSP camera viewer",
      },
      { name: "application-name", content: "GrowCam PC" },
      { property: "og:type", content: "website" },
      { property: "og:site_name", content: "GrowCam PC" },
      { name: "twitter:card", content: "summary" },
    ],
    colorMode: {
      defaultMode: "dark",
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "GrowCam PC",
      logo: {
        alt: "GrowCam PC logo",
        src: "img/growcam-mark.svg",
      },
      items: [
        { type: "docSidebar", sidebarId: "guideSidebar", position: "left", label: "Guide" },
        { to: "/docs/growcam-c4-setup", label: "GrowCam C4 setup", position: "left" },
        { to: "/docs/dashboard", label: "Dashboard", position: "left" },
        {
          href: "https://pypi.org/project/growcam-pc/",
          label: "PyPI",
          position: "right",
        },
        {
          href: "https://github.com/Nick2bad4u/growcam-pc",
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Use GrowCam PC",
          items: [
            { label: "GrowCam C4 setup", to: "/docs/growcam-c4-setup" },
            { label: "Install", to: "/docs/getting-started" },
            { label: "Dashboard", to: "/docs/dashboard" },
            { label: "CLI", to: "/docs/cli" },
          ],
        },
        {
          title: "Support",
          items: [
            { label: "Troubleshooting", to: "/docs/troubleshooting" },
            { label: "Security", to: "/docs/security" },
            { label: "Issues", href: "https://github.com/Nick2bad4u/growcam-pc/issues" },
          ],
        },
        {
          title: "Project",
          items: [
            { label: "GitHub", href: "https://github.com/Nick2bad4u/growcam-pc" },
            { label: "Changelog", href: "https://github.com/Nick2bad4u/growcam-pc/blob/main/CHANGELOG.md" },
            { label: "MIT License", href: "https://github.com/Nick2bad4u/growcam-pc/blob/main/LICENSE" },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Nick2bad4u. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ["bash", "powershell"],
    },
  },
};

export default config;
