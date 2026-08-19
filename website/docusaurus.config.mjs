import { themes as prismThemes } from "prism-react-renderer";

const yearFormatter = new Intl.DateTimeFormat("en", {
    year: "numeric",
});
const currentYear = yearFormatter.format();

/** @type {import("@docusaurus/types").Config} */
const config = {
    baseUrl: "/growcam-pc/",
    favicon: "img/growcam-mark.svg",
    i18n: {
        defaultLocale: "en",
        locales: ["en"],
    },
    onBrokenLinks: "throw",
    organizationName: "Nick2bad4u",
    presets: [
        [
            "classic",
            {
                blog: false,
                docs: {
                    editUrl:
                        "https://github.com/Nick2bad4u/growcam-pc/edit/main/website/",
                    showLastUpdateTime: true,
                    sidebarPath: "./sidebars.mjs",
                },
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
    projectName: "growcam-pc",
    tagline: "A local desktop dashboard for the VIVOSUN GrowCam C4",
    themeConfig: {
        colorMode: {
            defaultMode: "dark",
            respectPrefersColorScheme: true,
        },
        footer: {
            copyright: `Copyright © ${currentYear} Nick2bad4u. Built with Docusaurus.`,
            links: [
                {
                    items: [
                        {
                            label: "GrowCam C4 setup",
                            to: "/docs/growcam-c4-setup",
                        },
                        { label: "Install", to: "/docs/getting-started" },
                        { label: "Dashboard", to: "/docs/dashboard" },
                        { label: "CLI", to: "/docs/cli" },
                    ],
                    title: "Use GrowCam PC",
                },
                {
                    items: [
                        {
                            label: "Troubleshooting",
                            to: "/docs/troubleshooting",
                        },
                        { label: "Security", to: "/docs/security" },
                        {
                            href: "https://github.com/Nick2bad4u/growcam-pc/issues",
                            label: "Issues",
                        },
                    ],
                    title: "Support",
                },
                {
                    items: [
                        {
                            href: "https://github.com/Nick2bad4u/growcam-pc",
                            label: "GitHub",
                        },
                        {
                            href: "https://github.com/Nick2bad4u/growcam-pc/blob/main/CHANGELOG.md",
                            label: "Changelog",
                        },
                        {
                            href: "https://github.com/Nick2bad4u/growcam-pc/blob/main/LICENSE",
                            label: "MIT License",
                        },
                    ],
                    title: "Project",
                },
            ],
            style: "dark",
        },
        metadata: [
            { content: "#071418", name: "theme-color" },
            {
                content:
                    "Open-source VIVOSUN GrowCam C4 desktop viewer with local live video, 24-hour rewind, time-lapse previews, and camera file downloads.",
                name: "description",
            },
            {
                content:
                    "VIVOSUN GrowCam C4, GrowCam C4 PC viewer, GrowCam time-lapse, GrowCam rewind, RTSP camera viewer",
                name: "keywords",
            },
            { content: "GrowCam PC", name: "application-name" },
            { content: "website", property: "og:type" },
            { content: "GrowCam PC", property: "og:site_name" },
            { content: "summary", name: "twitter:card" },
        ],
        navbar: {
            items: [
                {
                    label: "Guide",
                    position: "left",
                    sidebarId: "guideSidebar",
                    type: "docSidebar",
                },
                {
                    label: "GrowCam C4 setup",
                    position: "left",
                    to: "/docs/growcam-c4-setup",
                },
                { label: "Dashboard", position: "left", to: "/docs/dashboard" },
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
            logo: {
                alt: "GrowCam PC logo",
                src: "img/growcam-mark.svg",
            },
            title: "GrowCam PC",
        },
        prism: {
            additionalLanguages: ["bash", "powershell"],
            darkTheme: prismThemes.dracula,
            theme: prismThemes.github,
        },
    },
    title: "GrowCam PC",
    trailingSlash: false,
    url: "https://nick2bad4u.github.io",
};

export default config;
