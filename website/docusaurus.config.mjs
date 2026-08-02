import { themes as prismThemes } from "prism-react-renderer";

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: "GrowCam PC",
  tagline: "Private, local-first access to your GrowCam camera",
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
        content: "Local live video, daily rewind, time-lapse previews, and camera file downloads for VIVOSUN GrowCam cameras.",
      },
    ],
    colorMode: {
      defaultMode: "dark",
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "GrowCam PC",
      logo: {
        alt: "GrowCam PC",
        src: "img/growcam-mark.svg",
      },
      items: [
        { type: "docSidebar", sidebarId: "guideSidebar", position: "left", label: "Guide" },
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
